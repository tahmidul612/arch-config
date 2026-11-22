//! NVIDIA GPU readings through NVML (loaded at runtime; absent driver = no GPU data).

use std::collections::HashMap;

use nvml_wrapper::enum_wrappers::device::TemperatureSensor;
use nvml_wrapper::enums::device::UsedGpuMemory;
use nvml_wrapper::error::NvmlError;
use nvml_wrapper::{Device, Nvml};

#[derive(Default)]
pub struct GpuSystem {
    pub util: Option<f64>,
    pub mem_util: Option<f64>,
    pub vram_used_mb: Option<f64>,
    pub vram_total_mb: Option<f64>,
    pub power_w: Option<f64>,
    pub temp_c: Option<f64>,
}

/// Per process: VRAM in MiB and SM (3D/compute) utilisation in percent.
#[derive(Default)]
pub struct GpuProc {
    pub vram_mb: f64,
    pub sm_pct: f64,
}

#[derive(Default)]
pub struct GpuSample {
    pub system: GpuSystem,
    pub procs: HashMap<u32, GpuProc>,
}

#[derive(Default)]
pub struct Gpu {
    nvml: Option<Nvml>,
    /// NVML timestamp of the newest per-process sample already consumed.
    last_seen: u64,
}

const MIB: f64 = 1_048_576.0;

impl Gpu {
    /// Reads device 0. A failure drops the NVML handle so the next call re-initialises it;
    /// a driver upgrade or a resume from suspend invalidates an open handle.
    pub fn sample(&mut self) -> GpuSample {
        if self.nvml.is_none() {
            self.nvml = Nvml::init().ok();
        }
        let Some(nvml) = &self.nvml else { return GpuSample::default() };
        let sample = nvml.device_by_index(0).and_then(|dev| read(&dev, &mut self.last_seen));
        sample.unwrap_or_else(|_| {
            self.nvml = None;
            GpuSample::default()
        })
    }
}

fn read(dev: &Device, last_seen: &mut u64) -> Result<GpuSample, NvmlError> {
    // The utilisation query doubles as the health check: if it fails the handle is stale.
    let util = dev.utilization_rates()?;
    let mem = dev.memory_info().ok();
    let system = GpuSystem {
        util: Some(f64::from(util.gpu)),
        mem_util: Some(f64::from(util.memory)),
        vram_used_mb: mem.as_ref().map(|m| m.used as f64 / MIB),
        vram_total_mb: mem.as_ref().map(|m| m.total as f64 / MIB),
        power_w: dev.power_usage().ok().map(|mw| f64::from(mw) / 1000.0),
        temp_c: dev.temperature(TemperatureSensor::Gpu).ok().map(f64::from),
    };

    let mut procs: HashMap<u32, GpuProc> = HashMap::new();
    let lists = [dev.running_graphics_processes(), dev.running_compute_processes()];
    for info in lists.into_iter().flat_map(Result::unwrap_or_default) {
        let entry = procs.entry(info.pid).or_default();
        if let UsedGpuMemory::Used(bytes) = info.used_gpu_memory {
            // A process using both graphics and compute is listed twice with the same total.
            entry.vram_mb = entry.vram_mb.max(bytes as f64 / MIB);
        }
    }
    // Average SM % over every driver sample since the last read, not one instant.
    let samples = match dev.process_utilization_stats(*last_seen) {
        Ok(s) => s,
        Err(NvmlError::NotFound | NvmlError::NoData | NvmlError::NotSupported) => Vec::new(),
        Err(e) => return Err(e),
    };
    let mut sm: HashMap<u32, (u32, u32)> = HashMap::new();
    for s in &samples {
        *last_seen = (*last_seen).max(s.timestamp);
        let e = sm.entry(s.pid).or_default();
        e.0 += s.sm_util;
        e.1 += 1;
    }
    for (pid, (sum, n)) in sm {
        procs.entry(pid).or_default().sm_pct = f64::from(sum) / f64::from(n);
    }
    Ok(GpuSample { system, procs })
}
