// A copier dans :
//   C:\Program Files\Adobe\Adobe After Effects 2025\Support Files\Scripts\ScriptUI Panels\
// puis, dans After Effects : menu Fenetre > CameraSim_loader.jsx
// Il charge le vrai panneau depuis le dossier du projet : tu peux modifier
// CameraSim.jsx sans le recopier.
$.global.CAMERASIM_HOST = this;
// Indiquer ici le dossier ou se trouve CamSensorSim-AE :
$.global.CAMERASIM_ROOT = "C:\\path\\to\\CamSensorSim-AE";
$.evalFile(File($.global.CAMERASIM_ROOT + "\\ae\\CameraSim.jsx"));
