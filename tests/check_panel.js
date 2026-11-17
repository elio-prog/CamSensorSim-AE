// Verification du panneau hors d'After Effects, avec le moteur JScript de Windows
// (meme generation de langage que l'ExtendScript d'AE).
//
//   cscript //nologo //E:JScript tests\check_panel.js
//
// 1. Le fichier du panneau se compile (aucune erreur de syntaxe).
// 2. Le panneau s'execute jusqu'au bout avec une fausse interface et de faux objets AE,
//    construit un controle par reglage, et produit un fichier de travail correct
//    quand on clique sur "Apercu" et "Rendre".
// Cela ne remplace pas un essai dans After Effects : l'interface reelle n'est pas testee.

var fso = new ActiveXObject("Scripting.FileSystemObject");
var here = fso.GetParentFolderName(WScript.ScriptFullName);
var rootDir = fso.GetParentFolderName(here);
var failures = 0;

function check(name, ok, detail) {
    if (!ok) { failures++; }
    WScript.Echo((ok ? "OK    " : "ECHEC ") + name + " : " + detail);
}

function slurp(path) {
    var s = new ActiveXObject("ADODB.Stream");
    s.Type = 2; s.Charset = "utf-8"; s.Open(); s.LoadFromFile(path);
    var t = s.ReadText(); s.Close();
    return t;
}

var source = slurp(rootDir + "\\ae\\CameraSim.jsx");

// ---- 1. compilation
var compiled = true, compileError = "";
try { new Function(source); } catch (e) { compiled = false; compileError = e.message; }
check("syntaxe du panneau", compiled, compiled ? "le fichier se compile" : compileError);

// ---- 2. execution avec de faux objets
var written = {};          // chemin -> texte ecrit
var executed = [];
var alerts = [];
var store = {};

var fileRegistry = {};     // chemin -> dernier objet fichier cree (pour signaler une ecriture faite apres coup)
function FakeFile(path) {
    if (!(this instanceof FakeFile)) { return new FakeFile(path); }
    path = String(path).replace(/\//g, "\\");
    fileRegistry[path] = this;
    this.fsName = path;
    this.name = encodeURI(path.substring(path.lastIndexOf("\\") + 1));
    this.parent = { fsName: path.substring(0, path.lastIndexOf("\\")) };
    this.exists = fso.FileExists(path) || (written[path] !== undefined);
    this._buf = "";
}
FakeFile.prototype.open = function (mode) { this._mode = mode; return true; };
FakeFile.prototype.read = function () { return (written[this.fsName] !== undefined) ? written[this.fsName] : slurp(this.fsName); };
FakeFile.prototype.write = function (t) { this._buf += t; };
FakeFile.prototype.close = function () { if (this._mode === "w") { written[this.fsName] = this._buf; } };
FakeFile.prototype.execute = function () { executed.push(this.fsName); return true; };

function FakeFolder(path) {
    if (!(this instanceof FakeFolder)) { return new FakeFolder(path); }
    this.fsName = String(path).replace(/\//g, "\\");
    this.exists = true;
}
FakeFolder.prototype.create = function () { return true; };
FakeFolder.selectDialog = function () { return null; };

function Ctrl(type, text, items) {
    this.type = type; this.text = text || ""; this.value = false; this.enabled = true;
    this.preferredSize = {}; this.children = []; this.selection = null; this.items = [];
    if (items) { for (var k = 0; k < items.length; k++) { this.items.push({ index: k, text: items[k] }); } }
    this.layout = { layout: function () {}, resize: function () {} };
}
Ctrl.prototype.add = function (type, bounds, arg) {
    var c = new Ctrl(type, (typeof arg === "string") ? arg : "", (arg instanceof Array) ? arg : null);
    this.children.push(c);
    return c;
};
function Panel() {}
function Window() { Ctrl.call(this, "window"); }
Window.prototype = new Ctrl("window");
Window.prototype.center = function () {};
Window.prototype.show = function () {};
Window.prototype.close = function () {};
function CompItem() {}
function FootageItem() {}
function ImportOptions() {}

var seqFile = "D:\\plans\\tete\\test_sh01_beauty_v001_1001.exr";
var layer = { source: new FootageItem(), name: "tete beauty", startTime: 0, stretch: 100, inPoint: 0, outPoint: 4,
              timeRemapEnabled: false };
layer.source.file = new FakeFile(seqFile);
layer.source.frameRate = 25;
layer.source.duration = 4;        // 100 images
var comp = new CompItem();
comp.selectedLayers = [layer]; comp.frameRate = 25; comp.frameDuration = 0.04;
comp.time = 0.36;                 // image 9 -> fichier 1010
comp.workAreaStart = 0.4; comp.workAreaDuration = 0.8;   // images 10 a 29 -> 1011 a 1030

var app = {
    settings: {
        haveSetting: function (s, k) { return store[k] !== undefined; },
        getSetting: function (s, k) { return store[k]; },
        saveSetting: function (s, k, v) { store[k] = v; }
    },
    project: { activeItem: comp, file: null },
    scheduleTask: function () { return 1; }, cancelTask: function () {},
    beginUndoGroup: function () {}, endUndoGroup: function () {}
};
var $ = { global: { CAMERASIM_ROOT: rootDir }, fileName: rootDir + "\\ae\\CameraSim.jsx" };
function alert(m) { alerts.push(m); }
var File = FakeFile, Folder = FakeFolder;

var hostWin = null;
var ran = true, runError = "";
try {
    // Le panneau est une fonction appelee avec "this" : on lui passe un objet quelconque
    // (pas un Panel), il cree donc sa propre fenetre, que l'on capture.
    var OrigWindow = Window;
    Window = function () { OrigWindow.call(this); hostWin = this; };
    Window.prototype = OrigWindow.prototype;
    (new Function("File", "Folder", "app", "$", "alert", "Panel", "Window", "CompItem", "FootageItem", "ImportOptions",
                  source)).call({}, FakeFile, FakeFolder, app, $, alert, Panel, Window, CompItem, FootageItem, ImportOptions);
} catch (e2) { ran = false; runError = e2.message; }
check("execution du panneau", ran && hostWin !== null, ran ? "interface construite sans erreur" : runError);

function walk(c, out) { out.push(c); for (var k = 0; k < c.children.length; k++) { walk(c.children[k], out); } return out; }

if (ran && hostWin) {
    var all = walk(hostWin, []);
    var schema = eval("(" + slurp(rootDir + "\\ae\\settings_schema.json") + ")");
    var nItems = 0, g;
    for (g = 0; g < schema.groups.length; g++) { nItems += schema.groups[g].items.length; }
    var tabs = 0, inputs = 0, buttons = {}, k;
    for (k = 0; k < all.length; k++) {
        if (all[k].type === "tab") { tabs++; }
        if (all[k].type === "edittext" || all[k].type === "checkbox" || all[k].type === "dropdownlist") { inputs++; }
        if (all[k].type === "button") { buttons[all[k].text] = all[k]; }
    }
    // inputs = un par reglage + la liste des cameras + le champ du dossier de sortie
    check("un controle par reglage", tabs === schema.groups.length && inputs === nItems + 2,
          tabs + " onglets, " + (inputs - 2) + " controles pour " + nItems + " reglages");

    function lastJob() {
        var p, best = null;
        for (p in written) { if (/\.json$/.test(p) && (best === null || p > best)) { best = p; } }
        return best ? eval("(" + written[best] + ")") : null;
    }

    buttons["Apercu (image courante)"].onClick();
    var jb = lastJob();
    check("clic sur Apercu", alerts.length === 0 && jb && jb.mode === "preview" && jb.first === 1010 &&
          jb.src_file === seqFile && jb.profile === schema.profiles[0].key && executed.length === 1 && /\.vbs$/.test(executed[0]),
          jb ? ("travail ecrit : image " + jb.first + ", camera " + jb.profile + ", lanceur .vbs execute")
             : ("aucun travail ; messages : " + alerts.join(" | ")));
    var batText = "", p2;
    for (p2 in written) { if (/\.bat$/.test(p2)) { batText = written[p2]; } }
    check("commande du moteur", batText.indexOf("python.exe\" -I \"") > 0 && batText.indexOf("engine\\camsim\\job.py\" --job \"") > 0 &&
          batText.indexOf("rem CameraSim") === 0, "python -I job.py --job <fichier>, sortie redirigee vers un journal");
    check("reglages par defaut non envoyes", jb && (function () { var n = 0, kk; for (kk in jb.settings) { n++; } return n; })() === 0,
          "seuls les reglages modifies sont transmis");

    // Un clic pendant qu'un calcul tourne doit etre refuse.
    buttons["Rendre la zone de travail"].onClick();
    check("un seul calcul a la fois", alerts.length === 1 && executed.length === 1, alerts.length ? alerts[0] : "pas de message");

    // Fin du calcul (le moteur ecrit "state=done") : le panneau redevient libre.
    function finishCurrent() {
        var j = lastJob(), key = j.status.replace(/\//g, "\\");
        written[key] = "state=done\nmode=preview\noutput=" + j.status + ".png\n";
        if (fileRegistry[key]) { fileRegistry[key].exists = true; }
        $.global.__cameraSimPoll();
    }

    // Calque video : image numero = temps * cadence, a partir de 0.
    finishCurrent();
    layer.source.file = new FakeFile("D:\\plans\\clip.mov");
    var before = executed.length;
    buttons["Apercu (image courante)"].onClick();
    jb = lastJob();
    check("calque video", executed.length === before + 1 && jb.src_file === "D:\\plans\\clip.mov" && jb.first === 9 && jb.still === false,
          jb ? ("video, image " + jb.first + " (0,36 s a 25 im/s)") : alerts.join(" | "));

    // Image fixe (PNG) : image 0, marquee comme fixe.
    finishCurrent();
    layer.source.file = new FakeFile("D:\\plans\\logo_02.png");
    layer.source.mainSource = { isStill: true };
    before = executed.length;
    buttons["Apercu (image courante)"].onClick();
    jb = lastJob();
    check("image fixe", executed.length === before + 1 && jb.first === 0 && jb.still === true,
          jb ? ("image " + jb.first + ", fixe : " + jb.still) : alerts.join(" | "));

    // Format refuse.
    finishCurrent();
    layer.source.file = new FakeFile("D:\\plans\\plan.psd");
    layer.source.mainSource = { isStill: true };
    var nAlerts = alerts.length;
    buttons["Apercu (image courante)"].onClick();
    check("format refuse", alerts.length === nAlerts + 1 && /non pris en charge/.test(alerts[alerts.length - 1]),
          alerts.length > nAlerts ? alerts[alerts.length - 1].split("\n")[0] : "pas de message");
}

WScript.Echo("");
WScript.Echo(failures === 0 ? "tous les controles du panneau passent" : failures + " controle(s) en echec");
WScript.Quit(failures === 0 ? 0 : 1);
