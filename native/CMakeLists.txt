# CameraSim natif : coeur de calcul, outil de comparaison, effet After Effects.
#   cmake -S native -B native/build -G "Visual Studio 18 2026" -A x64 -DAE_SDK=<dossier Examples du SDK>
#   cmake --build native/build --config Release
cmake_minimum_required(VERSION 3.20)
project(CameraSimNative CXX)

# Bibliotheques d'execution integrees au module (pas besoin de Visual Studio sur le PC qui l'utilise).
set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(MSVC)
  add_compile_options(/O2 /fp:precise /openmp /utf-8 /W3)
endif()

add_library(camsim_core STATIC core/camsim.cpp core/util.cpp core/post.cpp core/frame.cpp core/shutter.cpp core/log.cpp gpu/gpu.cpp gpu/gpu_flow.cpp gpu/gpu_analog.cpp)
target_include_directories(camsim_core PUBLIC core)

# ------------------------------------------------------------------ carte graphique NVIDIA (facultatif)
# Les noyaux (gpu/kernels.cu) sont compiles par NVRTC (sans Visual Studio) pour chaque generation de carte,
# et integres au module. Le module charge le pilote NVIDIA a l'execution : il fonctionne aussi sans carte NVIDIA.
set(CUDA_TOOLKIT "C:/Program Files/NVIDIA GPU Computing Toolkit/CUDA/v12.9" CACHE PATH "Dossier du CUDA Toolkit")
if(EXISTS "${CUDA_TOOLKIT}/include/nvrtc.h")
  add_executable(build_kernels gpu/build_kernels.cpp)
  target_include_directories(build_kernels PRIVATE "${CUDA_TOOLKIT}/include")
  target_link_libraries(build_kernels PRIVATE "${CUDA_TOOLKIT}/lib/x64/nvrtc.lib")
  set(KDATA ${CMAKE_CURRENT_BINARY_DIR}/gpu_kernels_data.h)
  add_custom_command(
    OUTPUT ${KDATA}
    COMMAND ${CMAKE_COMMAND} -E env "PATH=${CUDA_TOOLKIT}/bin" $<TARGET_FILE:build_kernels>
            ${CMAKE_CURRENT_SOURCE_DIR}/gpu/kernels.cu ${CMAKE_CURRENT_SOURCE_DIR}/gpu/gpu_params.h ${KDATA}
    DEPENDS build_kernels gpu/kernels.cu gpu/gpu_params.h
    COMMENT "Compilation des noyaux de la carte graphique (NVRTC)")
  add_custom_target(gpu_kernels DEPENDS ${KDATA})
  add_dependencies(camsim_core gpu_kernels)
  target_include_directories(camsim_core PRIVATE "${CUDA_TOOLKIT}/include" ${CMAKE_CURRENT_BINARY_DIR})
  target_compile_definitions(camsim_core PRIVATE CAMSIM_GPU)
  message(STATUS "Carte graphique : CUDA ${CUDA_TOOLKIT}")
else()
  message(STATUS "CUDA Toolkit absent : module construit pour le processeur seulement")
endif()

add_executable(camsim_cli cli/main.cpp)
target_link_libraries(camsim_cli PRIVATE camsim_core)

# ------------------------------------------------------------------ effet After Effects
set(AE_SDK "" CACHE PATH "Dossier Examples du SDK After Effects")
if(AE_SDK AND EXISTS "${AE_SDK}/Headers/AE_Effect.h")
  # Un module .aex par effet. Meme recette que les exemples d'Adobe pour la description (PiPL) :
  # preprocesseur, PiPLTool, preprocesseur.
  function(add_ae_effect NAME SRC PIPL)
    set(PIPL_SRC ${CMAKE_CURRENT_SOURCE_DIR}/ae/${PIPL})
    set(PIPL_RR  ${CMAKE_CURRENT_BINARY_DIR}/${NAME}PiPL.rr)
    set(PIPL_RRC ${CMAKE_CURRENT_BINARY_DIR}/${NAME}PiPL.rrc)
    set(PIPL_RC  ${CMAKE_CURRENT_BINARY_DIR}/${NAME}PiPL.rc)
    add_custom_command(
      OUTPUT ${PIPL_RC}
      COMMAND cl /nologo /I "${AE_SDK}/Headers" /EP "${PIPL_SRC}" > "${PIPL_RR}"
      COMMAND "${AE_SDK}/Resources/PiPLtool" "${PIPL_RR}" "${PIPL_RRC}"
      COMMAND cl /nologo /D "MSWindows" /EP "${PIPL_RRC}" > "${PIPL_RC}"
      DEPENDS ${PIPL_SRC}
      COMMENT "Description de l'effet ${NAME} (PiPL)")
    add_library(${NAME} SHARED ae/${SRC} ${PIPL_RC})
    set_source_files_properties(${PIPL_RC} PROPERTIES GENERATED TRUE)
    target_include_directories(${NAME} PRIVATE
      ${AE_SDK}/Headers ${AE_SDK}/Headers/SP ${AE_SDK}/Headers/Win ${AE_SDK}/Resources ${AE_SDK}/Util)
    target_compile_definitions(${NAME} PRIVATE MSWindows WIN32 _WINDOWS)
    target_link_libraries(${NAME} PRIVATE camsim_core)
    set_target_properties(${NAME} PROPERTIES SUFFIX ".aex" PREFIX "")
  endfunction()
  add_ae_effect(CameraSim CameraSim.cpp CameraSimPiPL.r)
  add_ae_effect(CameraSimAnalog CameraSimAnalog.cpp CameraSimAnalogPiPL.r)
else()
  message(STATUS "AE_SDK non indique : seul le coeur et l'outil de comparaison sont construits")
endif()
