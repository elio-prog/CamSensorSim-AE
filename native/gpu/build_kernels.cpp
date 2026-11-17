// Construction : compile native/gpu/kernels.cu avec NVRTC (compilateur de NVIDIA, sans Visual Studio) pour
// chaque generation de carte NVIDIA, et ecrit un en-tete C++ contenant les binaires et du PTX generique.
//   build_kernels <kernels.cu> <gpu_params.h> <sortie.h>
// Generations : Maxwell (sm_50) a Blackwell (sm_120). Le PTX (compute_50) est recompile par le pilote pour
// toute carte plus recente que la liste.
#include <nvrtc.h>

#include <cstdio>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>

static std::string read_file(const char* path) {
    std::ifstream f(path, std::ios::binary);
    std::stringstream ss;
    ss << f.rdbuf();
    return ss.str();
}

static bool compile(const std::string& src, const std::string& header, const std::string& arch, bool ptx,
                    std::vector<char>& out) {
    const char* hdr_src[] = {header.c_str()};
    const char* hdr_name[] = {"gpu_params.h"};
    nvrtcProgram prog;
    if (nvrtcCreateProgram(&prog, src.c_str(), "kernels.cu", 1, hdr_src, hdr_name) != NVRTC_SUCCESS) return false;
    const std::string a = "--gpu-architecture=" + arch;
    const char* opts[] = {a.c_str(), "--std=c++14", "-default-device", "--fmad=false"};
    const nvrtcResult r = nvrtcCompileProgram(prog, 4, opts);
    size_t log_size = 0;
    nvrtcGetProgramLogSize(prog, &log_size);
    if (log_size > 1) {
        std::string log(log_size, '\0');
        nvrtcGetProgramLog(prog, &log[0]);
        std::fprintf(stderr, "[%s] %s\n", arch.c_str(), log.c_str());
    }
    if (r != NVRTC_SUCCESS) {
        nvrtcDestroyProgram(&prog);
        return false;
    }
    size_t n = 0;
    if (ptx) {
        nvrtcGetPTXSize(prog, &n);
        out.resize(n);
        nvrtcGetPTX(prog, out.data());
    } else {
        nvrtcGetCUBINSize(prog, &n);
        out.resize(n);
        nvrtcGetCUBIN(prog, out.data());
    }
    nvrtcDestroyProgram(&prog);
    return true;
}

static void write_array(std::ofstream& o, const std::string& name, const std::vector<char>& data) {
    o << "static const unsigned char " << name << "[" << data.size() << "] = {";
    for (size_t i = 0; i < data.size(); ++i) {
        if (i % 24 == 0) o << "\n";
        o << (unsigned)(unsigned char)data[i] << ",";
    }
    o << "\n};\n";
}

int main(int argc, char** argv) {
    if (argc < 4) {
        std::fprintf(stderr, "usage : build_kernels kernels.cu gpu_params.h sortie.h\n");
        return 2;
    }
    const std::string src = read_file(argv[1]), header = read_file(argv[2]);
    struct Arch { int major, minor; };
    const Arch archs[] = {{5, 0}, {5, 2}, {6, 0}, {6, 1}, {7, 0}, {7, 5}, {8, 0}, {8, 6}, {8, 9}, {9, 0}, {10, 0}, {12, 0}};
    std::ofstream o(argv[3], std::ios::binary);
    o << "// Genere par native/gpu/build_kernels.cpp a partir de native/gpu/kernels.cu : ne pas modifier.\n#pragma once\n";
    std::vector<std::string> entries;
    for (const Arch& a : archs) {
        std::vector<char> bin;
        const std::string arch = "sm_" + std::to_string(a.major * 10 + a.minor);
        if (!compile(src, header, arch, false, bin)) {
            std::fprintf(stderr, "echec de la compilation pour %s\n", arch.c_str());
            return 1;
        }
        const std::string name = "CUBIN_" + arch;
        write_array(o, name, bin);
        entries.push_back("{" + std::to_string(a.major) + ", " + std::to_string(a.minor) + ", " + name + ", sizeof(" + name + ")}");
        std::printf("%s : %zu octets\n", arch.c_str(), bin.size());
    }
    std::vector<char> ptx;
    if (!compile(src, header, "compute_50", true, ptx)) {
        std::fprintf(stderr, "echec de la compilation du PTX\n");
        return 1;
    }
    ptx.push_back('\0');
    write_array(o, "KERNEL_PTX", ptx);
    o << "struct KernelImage { int major, minor; const unsigned char* data; unsigned long long size; };\n";
    o << "static const KernelImage KERNEL_IMAGES[] = {\n";
    for (const std::string& e : entries) o << "    " << e << ",\n";
    o << "};\nstatic const int KERNEL_IMAGE_COUNT = " << entries.size() << ";\n";
    std::printf("PTX : %zu octets\n", ptx.size());
    return 0;
}
