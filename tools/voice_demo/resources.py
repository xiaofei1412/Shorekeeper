"""Sample the worker's physical memory and whole-device NVIDIA memory."""
import subprocess
import threading
import time

import psutil


class Resources:
    def __init__(self):
        self.samples = []
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._sample, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def _sample(self):
        process = psutil.Process()
        while not self.stop_event.is_set():
            memory = psutil.virtual_memory()
            process_memory = process.memory_info()
            sample = {"monotonic_s": time.perf_counter(),
                      "rss_bytes": process_memory.rss,
                      "os_peak_working_set_bytes": getattr(process_memory, "peak_wset", None),
                      "system_available_bytes": memory.available}
            try:
                result = subprocess.run(
                    ["nvidia-smi", "--query-gpu=memory.used,memory.total,utilization.gpu",
                     "--format=csv,noheader,nounits", "--id=0"],
                    capture_output=True, text=True, timeout=3,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=True)
                used, total, utilization = map(int, result.stdout.strip().split(","))
                sample.update(gpu_device_used_mib=used, gpu_device_total_mib=total,
                              gpu_utilization_percent=utilization)
            except (OSError, subprocess.SubprocessError, ValueError) as exc:
                sample["gpu_sampling_error"] = str(exc)
            self.samples.append(sample)
            self.stop_event.wait(0.5)

    def __exit__(self, *args):
        self.stop_event.set()
        self.thread.join(timeout=5)

    def summary(self):
        return {"sample_count": len(self.samples), "nominal_interval_s": 0.5,
                "peak_process_rss_bytes": max((s["rss_bytes"] for s in self.samples), default=None),
                "os_peak_working_set_bytes": max((s["os_peak_working_set_bytes"] or 0 for s in self.samples), default=None),
                "min_system_available_bytes": min((s["system_available_bytes"] for s in self.samples), default=None),
                "peak_whole_gpu_used_mib": max((s.get("gpu_device_used_mib", 0) for s in self.samples), default=None),
                "gpu_scope": "whole device including other processes; not per-process VRAM"}
