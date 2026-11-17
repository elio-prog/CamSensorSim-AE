// Acces a la carte graphique NVIDIA (CUDA, interface du pilote chargee a l'execution).
// Aucune dependance : sans carte NVIDIA, ou avec un pilote trop ancien, available() renvoie faux et
// le calcul reste sur le processeur.
#pragma once
#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

namespace camsim {
namespace gpu {

bool available();                 // pilote charge, carte trouvee, noyaux charges (essaye une seule fois)
std::string describe();           // nom de la carte ou raison de l'absence

typedef unsigned long long DevPtr;

// Une session de calcul : contexte de la carte, file de commandes, memoire liberee a la fin.
// Une session par image calculee (plusieurs peuvent tourner en meme temps, rendu multi-images).
class Session {
public:
    Session();
    ~Session();
    bool ok() const { return ok_; }
    DevPtr alloc(size_t bytes);                       // 0 si echec (la session devient non ok)
    bool upload(DevPtr dst, const void* src, size_t bytes);
    bool download(void* dst, DevPtr src, size_t bytes);
    // Lance un noyau par son nom sur n elements (blocs de 256). args : adresses des arguments.
    bool launch(const char* kernel, size_t n, void** args);
    // Lance un noyau avec un nombre de blocs et de fils par bloc choisis.
    bool launch_grid(const char* kernel, unsigned grid, unsigned block, void** args, unsigned shared = 0);
    bool zero(DevPtr p, size_t bytes);                // remet a zero
    bool sync();
private:
    bool ok_ = false;
    void* stream_ = nullptr;
    std::vector<DevPtr> owned_;
    std::vector<size_t> sizes_;
};

}  // namespace gpu
}  // namespace camsim
