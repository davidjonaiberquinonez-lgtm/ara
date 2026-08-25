"""Detección de hardware (CPU vs GPU) — STAGED, no importado todavía.

Hoy ara_server.py está programado para recursos limitados (CPU compartida,
pools de conexión chicos, ThreadPoolExecutor conservador). Cuando llegue la
GDX, este módulo reemplaza esos valores fijos por un perfil detectado en vivo,
en vez de hardcodear "estamos en GPU" a mano.

Sin dependencias nuevas: usa nvidia-smi por subprocess (siempre disponible en
un host con GPU NVIDIA) en vez de requerir torch/pynvml solo para esto.

Uso futuro (NO activar hoy):
    from hardware_detect import detectar_perfil
    perfil = detectar_perfil()
    if perfil.tiene_gpu:
        max_workers = perfil.gpu_count * 4
    else:
        max_workers = min(4, perfil.cpu_count)
"""
import os
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class PerfilHardware:
    tiene_gpu: bool
    gpu_count: int
    gpu_nombres: list
    gpu_vram_mb: list
    cpu_count: int


def _detectar_gpu_nvidia_smi() -> tuple:
    """Devuelve (nombres, vram_mb) vía nvidia-smi, o listas vacías si no hay GPU/driver."""
    try:
        salida = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5, check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return [], []

    nombres, vram = [], []
    for linea in salida.stdout.strip().splitlines():
        partes = [p.strip() for p in linea.split(",")]
        if len(partes) == 2:
            nombres.append(partes[0])
            try:
                vram.append(int(partes[1]))
            except ValueError:
                vram.append(0)
    return nombres, vram


def detectar_perfil() -> PerfilHardware:
    nombres, vram = _detectar_gpu_nvidia_smi()
    return PerfilHardware(
        tiene_gpu=len(nombres) > 0,
        gpu_count=len(nombres),
        gpu_nombres=nombres,
        gpu_vram_mb=vram,
        cpu_count=os.cpu_count() or 1,
    )


if __name__ == "__main__":
    p = detectar_perfil()
    if p.tiene_gpu:
        print(f"GPU detectada: {p.gpu_count}x {', '.join(p.gpu_nombres)} (VRAM: {p.gpu_vram_mb} MB)")
    else:
        print(f"Sin GPU (nvidia-smi no disponible o sin GPU NVIDIA). CPU cores: {p.cpu_count}")
