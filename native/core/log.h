// Journal des temps de calcul : sur la sortie d'erreur si la variable d'environnement CAMSIM_TIMING existe,
// et dans un fichier si set_log_file() a ete appele (effets After Effects : %TEMP%\CameraSim_temps.txt).
#pragma once
#include <chrono>

namespace camsim {

void set_log_file(const char* path);
bool log_enabled();
void log(const char* fmt, ...);          // une ligne (le saut de ligne est ajoute), sure entre fils

// Chronometre par etapes : lap("nom") note le temps depuis l'etape precedente.
struct Timer {
    std::chrono::steady_clock::time_point t0 = std::chrono::steady_clock::now(), t = t0;
    bool on = log_enabled();
    void lap(const char* name);
    double total_ms() const;
};

}  // namespace camsim
