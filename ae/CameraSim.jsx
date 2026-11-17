// CameraSim - panneau After Effects.
// Fait passer le calque selectionne (EXR ACEScg, PNG / TIFF / JPEG ou video) dans une camera simulee.
// Le calcul est fait par le moteur Python du dossier CameraSim_AE ; ce panneau ne fait
// que preparer le travail, le lancer sans bloquer AE, suivre l'avancement et importer le resultat.
//
// Installation : voir CameraSim_loader.jsx. On peut aussi lancer ce fichier par
// Fichier > Scripts > Executer le fichier de script.

(function (thisObj) {
    var SECTION = "CameraSimAE";
    var host = ($.global.CAMERASIM_HOST instanceof Panel) ? $.global.CAMERASIM_HOST : thisObj;

    // ------------------------------------------------------------------ outils
    function getS(k, d) { return app.settings.haveSetting(SECTION, k) ? app.settings.getSetting(SECTION, k) : d; }
    function setS(k, v) { app.settings.saveSetting(SECTION, k, String(v)); }
    function q(s) { return "\"" + s + "\""; }
    function trim(s) { return String(s).replace(/^\s+|\s+$/g, ""); }
    function has(o, k) { return Object.prototype.hasOwnProperty.call(o, k); }
    function inList(list, v) { for (var i = 0; i < list.length; i++) { if (list[i] === v) { return true; } } return false; }

    function readText(f) {
        f.encoding = "UTF-8";
        if (!f.open("r")) { return null; }
        var t = f.read();
        f.close();
        return t;
    }

    // systemEncoding = true : encodage par defaut de Windows (necessaire pour un fichier .vbs).
    function writeText(f, text, systemEncoding) {
        if (!systemEncoding) { f.encoding = "UTF-8"; }
        if (!f.open("w")) { return false; }
        f.write(text);
        f.close();
        return true;
    }

    // ExtendScript n'a pas d'objet JSON : petit serialiseur, sortie en ASCII pur.
    function jsonString(s) {
        var out = "\"", c, code, i;
        s = String(s);
        for (i = 0; i < s.length; i++) {
            c = s.charAt(i);
            code = s.charCodeAt(i);
            if (c === "\"" || c === "\\") { out += "\\" + c; }
            else if (code === 10) { out += "\\n"; }
            else if (code === 13) { out += "\\r"; }
            else if (code === 9) { out += "\\t"; }
            else if (code < 32 || code > 126) { out += "\\u" + ("0000" + code.toString(16)).slice(-4); }
            else { out += c; }
        }
        return out + "\"";
    }

    function toJSON(v) {
        var parts = [], k, i;
        if (v === null || v === undefined) { return "null"; }
        if (typeof v === "boolean") { return v ? "true" : "false"; }
        if (typeof v === "number") { return isFinite(v) ? String(v) : "null"; }
        if (typeof v === "string") { return jsonString(v); }
        if (v instanceof Array) {
            for (i = 0; i < v.length; i++) { parts.push(toJSON(v[i])); }
            return "[" + parts.join(",") + "]";
        }
        for (k in v) { if (has(v, k)) { parts.push(jsonString(k) + ":" + toJSON(v[k])); } }
        return "{" + parts.join(",") + "}";
    }

    function parseJSON(text) { return eval("(" + text + ")"); }   // fichiers ecrits par l'outil lui-meme

    function fmt(v) {
        if (v === null || v === undefined) { return ""; }
        if (typeof v !== "number") { return String(v); }
        if (Math.round(v) === v) { return String(v); }
        return String(parseFloat(v.toPrecision(5)));
    }

    function stamp() {
        var d = new Date();
        function p(n) { return (n < 10 ? "0" : "") + n; }
        return String(d.getFullYear()).slice(2) + p(d.getMonth() + 1) + p(d.getDate()) + "_" +
               p(d.getHours()) + p(d.getMinutes()) + p(d.getSeconds());
    }

    function safeName(s) { return String(s).replace(/[^A-Za-z0-9_\-]+/g, "_").substring(0, 40); }

    // ------------------------------------------------------------------ dossier de l'outil et schema
    function findRoot() {
        if ($.global.CAMERASIM_ROOT) { return $.global.CAMERASIM_ROOT; }
        var saved = getS("root", "");
        if (saved !== "" && File(saved + "/engine/camsim/job.py").exists) { return saved; }
        try {
            var r = File($.fileName).parent.parent;
            if (File(r.fsName + "/engine/camsim/job.py").exists) { return r.fsName; }
        } catch (e) {}
        return "";
    }

    var root = findRoot();
    if (root === "") {
        var picked = Folder.selectDialog("Dossier CameraSim_AE (celui qui contient engine et ae)");
        if (!picked) { return; }
        root = picked.fsName;
    }
    setS("root", root);

    var schemaText = readText(File(root + "/ae/settings_schema.json"));
    if (schemaText === null) {
        alert("CameraSim : fichier ae/settings_schema.json introuvable dans\n" + root +
              "\n\nLance tools/export_schema.py pour le creer.");
        return;
    }
    var schema = parseJSON(schemaText);

    // ------------------------------------------------------------------ interface
    var win = (host instanceof Panel) ? host : new Window("palette", "CameraSim", undefined, { resizeable: true });
    win.orientation = "column";
    win.alignChildren = ["fill", "top"];
    win.spacing = 6;
    win.margins = 8;

    var gCam = win.add("group");
    gCam.add("statictext", undefined, "Camera :");
    var profileNames = [], i, j;
    for (i = 0; i < schema.profiles.length; i++) { profileNames.push(schema.profiles[i].name); }
    var ddProfile = gCam.add("dropdownlist", undefined, profileNames);
    ddProfile.alignment = ["fill", "center"];

    // Boutons en haut : sous les onglets, ils sortaient de la partie visible
    // d'un panneau peu haut (l'onglet le plus long compte 15 reglages).
    var gBtn1 = win.add("group");
    var bPreview = gBtn1.add("button", undefined, "Apercu (image courante)");
    var bRender = gBtn1.add("button", undefined, "Rendre la zone de travail");
    var gBtn2 = win.add("group");
    var bMutate = gBtn2.add("button", undefined, "Mutation");
    var bReset = gBtn2.add("button", undefined, "Remise a zero");
    var bSave = gBtn2.add("button", undefined, "Enregistrer");
    var bLoad = gBtn2.add("button", undefined, "Charger");
    bMutate.helpTip = "Deregle trois reglages au hasard";
    bSave.helpTip = "Enregistre la camera et ses reglages dans un fichier";

    var gOut = win.add("group");
    gOut.add("statictext", undefined, "Sortie :");
    var eOut = gOut.add("edittext", undefined, getS("outRoot", ""));
    eOut.alignment = ["fill", "center"];
    eOut.helpTip = "Vide = dossier CameraSim_out a cote du projet AE";
    var bOut = gOut.add("button", undefined, "...");
    bOut.preferredSize.width = 30;

    var stStatus = win.add("statictext", undefined, "Selectionne un calque (images ou video) puis Apercu.", { truncate: "middle" });
    stStatus.alignment = ["fill", "top"];

    var tabs = win.add("tabbedpanel");
    tabs.alignChildren = ["fill", "fill"];
    var controls = {};      // cle -> { item, ctrl, row }
    var order = [];

    function makeRow(parent, item) {
        var row = parent.add("group");
        row.orientation = "row";
        row.alignChildren = ["left", "center"];
        var ctrl, k;
        if (item.type === "bool") {
            ctrl = row.add("checkbox", undefined, item.label);
            ctrl.value = (item["default"] === true);
        } else {
            var lab = row.add("statictext", undefined, item.label);
            lab.preferredSize.width = 250;
            if (item.type === "choice") {
                var names = [];
                for (k = 0; k < item.choices.length; k++) { names.push(item.choices[k] === "" ? "(profil)" : item.choices[k]); }
                ctrl = row.add("dropdownlist", undefined, names);
                ctrl.preferredSize.width = 130;
            } else {
                ctrl = row.add("edittext", undefined, "");
                ctrl.preferredSize.width = 130;
            }
            if (item.hint) { lab.helpTip = item.hint; }
        }
        if (item.hint) { ctrl.helpTip = item.hint; }
        controls[item.key] = { item: item, ctrl: ctrl, row: row };
        order.push(item.key);
    }

    for (i = 0; i < schema.groups.length; i++) {
        var tab = tabs.add("tab", undefined, schema.groups[i].title);
        tab.orientation = "column";
        tab.alignChildren = ["fill", "top"];
        tab.spacing = 3;
        tab.margins = 8;
        for (j = 0; j < schema.groups[i].items.length; j++) { makeRow(tab, schema.groups[i].items[j]); }
    }

    // ------------------------------------------------------------------ valeurs des reglages
    function currentProfile() {
        var k = ddProfile.selection ? ddProfile.selection.index : 0;
        return schema.profiles[k];
    }

    function setValue(key, v) {
        var c = controls[key], k;
        if (!c) { return; }
        if (c.item.type === "bool") { c.ctrl.value = (v === true); }
        else if (c.item.type === "choice") {
            var sel = 0;
            for (k = 0; k < c.item.choices.length; k++) { if (c.item.choices[k] === (v === null ? "" : v)) { sel = k; } }
            c.ctrl.selection = sel;
        } else { c.ctrl.text = fmt(v); }
    }

    function resetAll() { for (var n = 0; n < order.length; n++) { setValue(order[n], controls[order[n]].item["default"]); } }

    // Renvoie { ok, value, isDefault } pour un reglage.
    function getValue(key) {
        var c = controls[key], d = c.item["default"], v, t;
        if (c.item.type === "bool") { v = (c.ctrl.value === true); return { ok: true, value: v, isDefault: v === d }; }
        if (c.item.type === "choice") {
            v = c.item.choices[c.ctrl.selection ? c.ctrl.selection.index : 0];
            return { ok: true, value: v, isDefault: v === (d === null ? "" : d) };
        }
        t = trim(c.ctrl.text);
        if (c.item.type === "text") { return { ok: true, value: t, isDefault: t === d }; }
        if (t === "") { return { ok: true, value: null, isDefault: true }; }
        v = parseFloat(t.replace(",", "."));
        if (isNaN(v)) { return { ok: false, value: null, isDefault: false }; }
        if (c.item.integer) { v = Math.round(v); }
        return { ok: true, value: v, isDefault: v === d };
    }

    // Reglages qui different des valeurs par defaut, pour la famille de camera choisie.
    function collectSettings(quiet) {
        var out = {}, kind = currentProfile().kind, n, key, r;
        for (n = 0; n < order.length; n++) {
            key = order[n];
            if (!inList(controls[key].item.kinds, kind)) { continue; }
            r = getValue(key);
            if (!r.ok) {
                if (!quiet) { alert("Valeur incorrecte pour \"" + controls[key].item.label + "\" : " + controls[key].ctrl.text); }
                return null;
            }
            if (!r.isDefault) { out[key] = r.value; }
        }
        return out;
    }

    function refreshEnabled() {
        var kind = currentProfile().kind;
        for (var n = 0; n < order.length; n++) { controls[order[n]].row.enabled = inList(controls[order[n]].item.kinds, kind); }
    }

    function saveState() {
        var s = collectSettings(true);
        if (s === null) { return; }
        setS("state", toJSON({ profile: currentProfile().key, settings: s }));
    }

    function applyState(state) {
        var k, n;
        resetAll();
        if (state && state.profile) {
            for (n = 0; n < schema.profiles.length; n++) { if (schema.profiles[n].key === state.profile) { ddProfile.selection = n; } }
        }
        if (state && state.settings) { for (k in state.settings) { if (has(state.settings, k)) { setValue(k, state.settings[k]); } } }
        refreshEnabled();
    }

    ddProfile.selection = 0;
    try { applyState(parseJSON(getS("state", "{}"))); } catch (eState) { applyState(null); }
    ddProfile.onChange = function () { refreshEnabled(); saveState(); };
    for (i = 0; i < order.length; i++) {
        if (controls[order[i]].item.type === "bool") { controls[order[i]].ctrl.onClick = saveState; }
        else { controls[order[i]].ctrl.onChange = saveState; }
    }

    bReset.onClick = function () { resetAll(); saveState(); stStatus.text = "Reglages remis a zero."; };

    bMutate.onClick = function () {
        var kind = currentProfile().kind, pool = [], n, key, it, names = [], lo, hi, v, pick;
        for (n = 0; n < order.length; n++) {
            it = controls[order[n]].item;
            if (it.mut && inList(it.kinds, kind)) { pool.push(order[n]); }
        }
        for (n = 0; n < 3 && pool.length > 0; n++) {
            pick = Math.floor(Math.random() * pool.length);
            key = pool[pick];
            pool.splice(pick, 1);
            it = controls[key].item;
            lo = it.mut[0]; hi = it.mut[1];
            // Plage tres etendue : tirage en echelle logarithmique.
            if (lo > 0 && hi / lo > 50) { v = lo * Math.pow(hi / lo, Math.random()); }
            else { v = lo + (hi - lo) * Math.random(); }
            if (it.integer) { v = Math.round(v); }
            setValue(key, parseFloat(v.toPrecision(3)));
            names.push(it.label);
        }
        saveState();
        stStatus.text = "Mutation : " + names.join(" ; ");
    };

    bSave.onClick = function () {
        var s = collectSettings(false);
        if (s === null) { return; }
        var dir = Folder(root + "/presets");
        if (!dir.exists) { dir.create(); }
        var f = File(dir.fsName + "/" + currentProfile().key + ".json").saveDlg("Enregistrer la camera", "JSON:*.json");
        if (!f) { return; }
        if (!writeText(f, toJSON({ profile: currentProfile().key, settings: s }), false)) { alert("Impossible d'ecrire " + f.fsName); return; }
        stStatus.text = "Enregistre : " + f.name;
    };

    bLoad.onClick = function () {
        var f = File(root + "/presets/x.json").openDlg("Charger une camera", "JSON:*.json");
        if (!f) { return; }
        try { applyState(parseJSON(readText(f))); saveState(); stStatus.text = "Charge : " + f.name; }
        catch (e) { alert("Fichier illisible :\n" + e.toString()); }
    };

    bOut.onClick = function () { var f = Folder.selectDialog("Dossier de sortie"); if (f) { eOut.text = f.fsName; setS("outRoot", f.fsName); } };
    eOut.onChange = function () { setS("outRoot", trim(eOut.text)); };

    // ------------------------------------------------------------------ calque source
    function readLayer() {
        var comp = app.project.activeItem;
        if (!(comp instanceof CompItem)) { alert("Ouvre une composition."); return null; }
        if (comp.selectedLayers.length !== 1) { alert("Selectionne exactement un calque."); return null; }
        var layer = comp.selectedLayers[0];
        if (!(layer.source instanceof FootageItem) || !layer.source.file) {
            alert("Le calque doit etre un fichier importe : images ou video (pas une precomposition ni un solide).");
            return null;
        }
        var file = layer.source.file;
        var fname = decodeURI(file.name);
        var ext = (fname.match(/\.([A-Za-z0-9]+)$/) || ["", ""])[1].toLowerCase();
        var isImage = /^(exr|png|tif|tiff|jpg|jpeg)$/.test(ext);
        var isVideo = /^(mov|mp4|m4v|avi|mkv|mxf|webm)$/.test(ext);
        if (!isImage && !isVideo) {
            alert("Format non pris en charge : ." + ext + "\n\nCameraSim lit les sequences ou images EXR, PNG, TIFF, JPEG et les videos (MOV, MP4, AVI, MKV, MXF, WEBM).\nPour une precomposition : la rendre d'abord en EXR ou PNG.");
            return null;
        }
        var still = false;
        try { still = layer.source.mainSource.isStill; } catch (eS) { still = false; }
        var m = fname.match(/^(.*?)(\d+)(\.[A-Za-z0-9]+)$/);
        // Numero de la premiere image : celui du fichier pour une sequence, 0 pour une video ou une image fixe.
        var firstNum = (isImage && !still && m) ? parseInt(m[2], 10) : 0;
        var fps = layer.source.frameRate || comp.frameRate;
        var count = still ? 1 : Math.max(1, Math.round(layer.source.duration * fps));
        var info = { comp: comp, layer: layer, file: file, firstNum: firstNum, fps: fps, count: count, still: still,
                     kind: (ext === "exr") ? "exr" : (isVideo ? "video" : "image") };
        info.frameAt = function (t) {
            if (still) { return firstNum; }
            var speed = (layer.stretch !== 0) ? 100 / layer.stretch : 1;
            var idx = Math.round((t - layer.startTime) * fps * speed);
            if (idx < 0) { idx = 0; }
            if (idx > count - 1) { idx = count - 1; }
            return info.firstNum + idx;
        };
        if (layer.timeRemapEnabled) { stStatus.text = "Attention : le remappage temporel du calque est ignore."; }
        return info;
    }

    function outputRoot() {
        var t = trim(eOut.text);
        if (t !== "") { return Folder(t); }
        if (app.project.file) { return Folder(app.project.file.parent.fsName + "/CameraSim_out"); }
        return Folder(root + "/out");
    }

    // ------------------------------------------------------------------ travail en cours
    var job = null;
    var previewWin = null;

    function readStatus(f) {
        var res = {}, n, k, lines, t;
        if (!f.exists) { return res; }
        t = readText(f);
        if (t === null) { return res; }
        lines = t.split(/\r?\n/);
        for (n = 0; n < lines.length; n++) {
            k = lines[n].indexOf("=");
            if (k > 0) { res[lines[n].substring(0, k)] = lines[n].substring(k + 1); }
        }
        return res;
    }

    function setBusy(busy) { bPreview.enabled = !busy; bRender.enabled = !busy; }

    function showPreview(png) {
        try { if (previewWin) { previewWin.close(); } } catch (e) {}
        previewWin = new Window("palette", "CameraSim - apercu", undefined, { resizeable: false });
        previewWin.margins = 4;
        previewWin.add("image", undefined, File(png));
        previewWin.show();
    }

    function importResult(j, st) {
        app.beginUndoGroup("CameraSim import");
        try {
            var io = new ImportOptions(File(st.output));
            io.sequence = !j.still;                  // image fixe : un seul fichier, importe comme image fixe
            if (!j.still) { io.forceAlphabetical = true; }
            var item = app.project.importFile(io);
            if (!j.still) { item.mainSource.conformFrameRate = j.fps; }
            var first = parseInt(st.first, 10);
            var nl = j.comp.layers.add(item);
            if (j.still) {
                nl.startTime = j.layer.startTime;
                nl.inPoint = j.layer.inPoint;
                nl.outPoint = j.layer.outPoint;
            } else {
                nl.startTime = j.layer.startTime + (first - j.firstNum) / j.fps;
            }
            nl.moveBefore(j.layer);
            nl.name = j.layer.name + " [" + j.profile + "]";
            stStatus.text = "Termine : " + st.frame + " image(s), calque ajoute au-dessus de la source." +
                            (parseInt(st.skipped, 10) > 0 ? " " + st.skipped + " image(s) illisible(s) ignoree(s)." : "");
        } catch (e) {
            stStatus.text = "Import echoue.";
            alert("Rendu termine mais import echoue :\n" + e.toString() + "\n\nFichiers : " + st.output);
        }
        app.endUndoGroup();
    }

    function finishJob(st) {
        try { app.cancelTask(job.task); } catch (eT) {}
        var j = job;
        job = null;
        setBusy(false);
        if (st.state === "error") {
            stStatus.text = "Erreur.";
            alert("CameraSim - erreur :\n" + (st.message || "inconnue") + "\n\nJournal : " + j.log.fsName);
            return;
        }
        if (j.mode === "preview") { stStatus.text = "Apercu pret (image " + j.first + ")."; showPreview(st.output); }
        else { importResult(j, st); }
    }

    $.global.__cameraSimPoll = function () {
        if (!job) { return; }
        var st = readStatus(job.status);
        if (st.state === "done" || st.state === "error") { finishJob(st); return; }
        if (st.state === "running") {
            var tot = parseInt(st.total, 10), fr = parseInt(st.frame, 10), rem = parseInt(st.remaining_s, 10);
            var txt = (st.phase ? st.phase + " : " : "") + fr + " / " + tot;
            if (rem > 0) { txt += "  (reste environ " + (rem >= 90 ? Math.round(rem / 60) + " min" : rem + " s") + ")"; }
            stStatus.text = txt;
        }
        if ((new Date().getTime() - job.started) > 20000 && !job.status.exists) {
            finishJob({ state: "error", message: "Le moteur n'a pas demarre." });
        }
    };

    function launch(mode) {
        if (job) { alert("Un calcul est deja en cours."); return; }
        var info = readLayer();
        if (!info) { return; }
        setValue("fps", info.fps);
        var settings = collectSettings(false);
        if (settings === null) { return; }

        var py = File(root + "/.venv/Scripts/python.exe");
        var engine = File(root + "/engine/camsim/job.py");
        if (!engine.exists) { alert("Moteur introuvable :\n" + engine.fsName); return; }
        if (!py.exists) { alert("Environnement Python manquant :\n" + py.fsName); return; }

        var outRoot = outputRoot();
        var jobs = Folder(outRoot.fsName + "/_travaux");
        if (!outRoot.exists && !outRoot.create()) { alert("Impossible de creer " + outRoot.fsName); return; }
        if (!jobs.exists && !jobs.create()) { alert("Impossible de creer " + jobs.fsName); return; }

        var tag = stamp(), profile = currentProfile();
        var base = jobs.fsName + "/" + mode + "_" + tag;
        var statusF = File(base + ".status.txt"), logF = File(base + ".log.txt");
        var jobF = File(base + ".json"), bat = File(base + ".bat"), vbs = File(base + ".vbs");

        var spec = { src_file: info.file.fsName, mode: mode, profile: profile.key, settings: settings, status: statusF.fsName,
                     still: info.still };
        if (mode === "preview") {
            spec.first = info.frameAt(info.comp.time);
            spec.preview_png = base + ".png";
        } else {
            var t0 = Math.max(info.comp.workAreaStart, info.layer.inPoint);
            var t1 = Math.min(info.comp.workAreaStart + info.comp.workAreaDuration, info.layer.outPoint) - info.comp.frameDuration;
            if (t1 < t0) { alert("La zone de travail ne recouvre pas le calque."); return; }
            spec.first = info.frameAt(t0);
            spec.last = info.frameAt(t1);
            spec.out_dir = outRoot.fsName + "/" + safeName(info.layer.name) + "_" + profile.key + "_" + tag;
        }

        if (!writeText(jobF, toJSON(spec), false)) {
            alert("Impossible d'ecrire dans " + jobs.fsName +
                  "\nActive : Preferences > Scripts et expressions > Autoriser les scripts a ecrire des fichiers.");
            return;
        }
        var cmd = [q(py.fsName), "-I", q(engine.fsName), "--job", q(jobF.fsName)].join(" ");
        // La premiere ligne "rem" absorbe une eventuelle marque d'encodage en tete de fichier.
        if (!writeText(bat, "rem CameraSim\r\n@echo off\r\nchcp 65001 >nul\r\n" + cmd + " > " + q(logF.fsName) + " 2>&1\r\n", false)) {
            alert("Impossible d'ecrire " + bat.fsName); return;
        }
        // Lancement non bloquant : system.callSystem figerait AE pendant tout le calcul.
        if (!writeText(vbs, "CreateObject(\"WScript.Shell\").Run \"cmd /c \"\"\"\"" + bat.fsName + "\"\"\"\"\", 7, False\r\n", true)) {
            alert("Impossible d'ecrire " + vbs.fsName); return;
        }
        if (!vbs.execute()) { alert("Impossible de lancer le moteur (" + vbs.fsName + ")."); return; }

        job = { mode: mode, status: statusF, log: logF, started: new Date().getTime(), comp: info.comp, layer: info.layer,
                fps: info.fps, firstNum: info.firstNum, first: spec.first, profile: profile.key, still: info.still };
        job.task = app.scheduleTask("$.global.__cameraSimPoll()", 1000, true);
        setBusy(true);
        stStatus.text = (mode === "preview") ? "Apercu de l'image " + spec.first + "..." :
                        "Rendu des images " + spec.first + " a " + spec.last + "...";
        saveState();
    }

    bPreview.onClick = function () { launch("preview"); };
    bRender.onClick = function () { launch("render"); };

    if (win instanceof Window) { win.center(); win.show(); }
    else { win.layout.layout(true); win.layout.resize(); win.onResizing = win.onSizing = function () { this.layout.resize(); }; }
})(this);
