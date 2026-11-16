//! Manual complete-operation benchmark of the existing Severe/Winter Qt renderer.
//! The installed application and extension are imported from an explicit checkout;
//! this executable contains measurement logic only and does not register native replacements.
#[path = "sounding_benchmark/metadata.rs"]
mod metadata;
#[path = "sounding_benchmark/process.rs"]
mod process;

use pyo3::{
    prelude::*,
    types::{PyBytes, PyDict, PyList},
};
use serde_json::{json, Value};
use std::{
    error::Error,
    io,
    path::PathBuf,
    time::{Instant, SystemTime, UNIX_EPOCH},
};

struct Options {
    source: PathBuf,
    input: PathBuf,
    output: PathBuf,
    image_mode: String,
    sounding_mode: String,
    repeat: usize,
    power_mode: String,
}

fn options() -> Result<Options, Box<dyn Error>> {
    let mut source = None;
    let mut input = None;
    let mut output = None;
    let mut image_mode = "hd".to_owned();
    let mut sounding_mode = "severe".to_owned();
    let mut repeat = 5;
    let mut power_mode = "unrecorded".to_owned();
    let mut args = std::env::args().skip(1);
    while let Some(arg) = args.next() {
        if arg == "--help" {
            println!("sounding_benchmark --source-root CHECKOUT --output-json FILE [--input SOUNDING] [--sounding-mode severe|winter] [--mode hd|uhd|lossless] [--repeat 5] [--power-mode DESCRIPTION]\nOne fresh process per mode. Outputs raw samples, source/environment identity and repeated-pixel checks. No timing threshold.");
            std::process::exit(0);
        }
        let value = args
            .next()
            .ok_or_else(|| io::Error::other(format!("missing value for {arg}")))?;
        match arg.as_str() {
            "--source-root" => source = Some(PathBuf::from(value)),
            "--input" => input = Some(PathBuf::from(value)),
            "--output-json" => output = Some(PathBuf::from(value)),
            "--sounding-mode" => sounding_mode = value,
            "--mode" => image_mode = value,
            "--repeat" => repeat = value.parse()?,
            "--power-mode" => power_mode = value,
            _ => return Err(io::Error::other(format!("unknown option: {arg}")).into()),
        }
    }
    let source = source
        .ok_or_else(|| io::Error::other("--source-root is required"))?
        .canonicalize()?;
    let input = input
        .unwrap_or_else(|| source.join("examples/soundings/hrrr_point_36.68N_95.66W_f018.npz"))
        .canonicalize()?;
    let output =
        std::path::absolute(output.ok_or_else(|| io::Error::other("--output-json is required"))?)?;
    if !matches!(image_mode.as_str(), "hd" | "uhd" | "lossless")
        || !matches!(sounding_mode.as_str(), "severe" | "winter")
        || !(2..=100).contains(&repeat)
    {
        return Err(io::Error::other("choose a valid mode and --repeat from 2 to 100").into());
    }
    if !input.is_file() || output.exists() || output == input {
        return Err(io::Error::other("input must be a file and output must be a new path").into());
    }
    std::fs::create_dir_all(output.parent().unwrap())?;
    Ok(Options {
        source,
        input,
        output,
        image_mode,
        sounding_mode,
        repeat,
        power_mode,
    })
}

fn median(values: &mut [f64]) -> f64 {
    values.sort_by(f64::total_cmp);
    let middle = values.len() / 2;
    if values.len().is_multiple_of(2) {
        (values[middle - 1] + values[middle]) / 2.
    } else {
        values[middle]
    }
}

fn measure(options: &Options, image_path: &PathBuf) -> Result<Value, Box<dyn Error>> {
    let source = metadata::source(&options.source)?;
    let machine = metadata::machine(&options.source, &options.power_mode);
    let import_started = Instant::now();
    let import_cpu = process::counters()?;
    Python::initialize();
    Python::attach(|py| -> Result<Value, Box<dyn Error>> {
        let sys = PyModule::import(py, "sys")?;
        sys.getattr("path")?
            .cast::<PyList>()
            .map_err(|error| io::Error::other(error.to_string()))?
            .insert(0, &options.source)?;
        let cli = PyModule::import(py, "sharpmod.rendering.cli")?;
        let module_path: PathBuf = cli.getattr("__file__")?.extract()?;
        if !module_path.canonicalize()?.starts_with(&options.source) {
            return Err(io::Error::other("renderer imports another checkout").into());
        }
        let extension = PyModule::import(py, "sharpmod_rs")?;
        let application = PyModule::import(py, "sharpmod")?;
        let app_version: String = application.getattr("__version__")?.extract()?;
        let native_version: String = extension.getattr("__version__")?.extract()?;
        if app_version != native_version {
            return Err(io::Error::other("application and native versions differ").into());
        }
        let backend = PyModule::import(py, "sharpmod.backends")?.call_method0("backend_info")?;
        if backend.get_item("active_backend")?.extract::<String>()? != "rust" {
            return Err(io::Error::other("benchmark requires the active Rust backend").into());
        }
        let import_seconds = import_started.elapsed().as_secs_f64();
        let import_after = process::counters()?;
        let native_path: PathBuf = extension.getattr("__file__")?.extract()?;
        let image_class = PyModule::import(py, "qtpy.QtGui")?.getattr("QImage")?;
        let rgba_format = image_class.getattr("Format")?.getattr("Format_RGBA8888")?;
        let kwargs = PyDict::new(py);
        kwargs.set_item("image_mode", &options.image_mode)?;
        kwargs.set_item("show_locator", false)?;
        if options.sounding_mode == "winter" {
            let view = PyDict::new(py);
            view.set_item("mode", "winter")?;
            view.set_item("chart", "streamwiseness")?;
            view.set_item("edits", PyList::empty(py))?;
            kwargs.set_item("view", view)?;
        }
        let json_module = PyModule::import(py, "json")?;
        let backend_json: String = json_module.call_method1("dumps", (backend,))?.extract()?;
        let versions = PyModule::import(py, "importlib.metadata")?;
        let mut dependencies = serde_json::Map::new();
        for name in ["numpy", "PySide6", "QtPy", "Pillow", "metpy"] {
            dependencies.insert(
                name.into(),
                json!(versions
                    .call_method1("version", (name,))?
                    .extract::<String>()?),
            );
        }
        let mut data = json!({
            "schema_version": 1, "workload": "complete-headless-sounding-render",
            "measured_at_unix_seconds": SystemTime::now().duration_since(UNIX_EPOCH)?.as_secs(),
            "source": source, "machine": machine,
            "application_version": app_version, "native_version": native_version,
            "native_sha256": metadata::hash(&std::fs::read(native_path)?),
            "python": sys.getattr("version")?.extract::<String>()?,
            "backend": serde_json::from_str::<Value>(&backend_json)?, "dependencies": dependencies,
            "fixture": {"path": options.input.strip_prefix(&options.source).unwrap_or(&options.input),
                "sha256": metadata::hash(&std::fs::read(&options.input)?)},
            "sounding_mode": options.sounding_mode, "image_mode": options.image_mode,
            "repeat": options.repeat, "import_seconds": import_seconds,
            "import_cpu_seconds": import_after["cpu_seconds"].as_f64().zip(import_cpu["cpu_seconds"].as_f64()).map(|(a,b)|a-b),
            "environment": {"QT_QPA_PLATFORM": std::env::var("QT_QPA_PLATFORM").ok(),
                "SHARPMOD_BACKEND": std::env::var("SHARPMOD_BACKEND").ok(),
                "RAYON_NUM_THREADS": std::env::var("RAYON_NUM_THREADS").ok()},
            "scope": "Complete cli.render in one fresh process: first render and subsequent calls, including Qt composition, PNG encoding and cleanup. Imports and validation are outside render samples. No provider network or reboot-cold claim. Memory is cumulative process high-water RSS, not per-render allocation or GPU memory.",
            "samples": [], "correctness": {"all_pngs_decode": true, "repeat_pixels_equal": true},
        });
        let render = cli.getattr("render")?;
        let mut expected = None;
        let mut warm = Vec::new();
        for index in 0..options.repeat {
            let before = process::counters()?;
            let started = Instant::now();
            render.call((&options.input, image_path), Some(&kwargs))?;
            let elapsed = started.elapsed().as_secs_f64();
            let after = process::counters()?;
            let image = image_class.call1((image_path,))?;
            if image.call_method0("isNull")?.extract::<bool>()? {
                return Err(io::Error::other("render did not produce a decodable PNG").into());
            }
            let width: u32 = image.call_method0("width")?.extract()?;
            let height: u32 = image.call_method0("height")?.extract()?;
            let rgba = image.call_method1("convertToFormat", (&rgba_format,))?;
            let raw = py
                .import("builtins")?
                .getattr("bytes")?
                .call1((rgba.call_method0("constBits")?,))?;
            let hash = metadata::hash(
                raw.cast::<PyBytes>()
                    .map_err(|error| io::Error::other(error.to_string()))?
                    .as_bytes(),
            );
            let identity = (width, height, hash.clone());
            if expected
                .as_ref()
                .is_some_and(|previous| previous != &identity)
            {
                return Err(io::Error::other(
                    "decoded pixels or dimensions changed between repeats",
                )
                .into());
            }
            expected = Some(identity);
            let row = json!({"index": index, "state": if index == 0 {"first"} else {"warm"},
                "elapsed_seconds": elapsed,
                "cpu_seconds": after["cpu_seconds"].as_f64().zip(before["cpu_seconds"].as_f64()).map(|(a,b)|a-b),
                "working_set_bytes": after["working_set_bytes"],
                "peak_working_set_bytes": after["peak_working_set_bytes"], "private_bytes": after["private_bytes"],
                "width": width, "height": height, "png_bytes": std::fs::metadata(image_path)?.len(),
                "rgba_sha256": hash});
            println!("{row}");
            data["samples"].as_array_mut().unwrap().push(row);
            if index > 0 {
                warm.push(elapsed);
            }
        }
        data["warm_median_seconds"] = json!(median(&mut warm));
        Ok(data)
    })
}

fn main() -> Result<(), Box<dyn Error>> {
    let options = options()?;
    std::env::set_var("QT_QPA_PLATFORM", "offscreen");
    std::env::set_var("QT_API", "pyside6");
    std::env::set_var("SHARPMOD_BACKEND", "rust");
    std::env::set_var("SHARPMOD_GEOCODER_URL", "off");
    let directory = std::env::temp_dir().join(format!(
        "sharpmod-render-benchmark-{}-{}",
        std::process::id(),
        SystemTime::now().duration_since(UNIX_EPOCH)?.as_nanos()
    ));
    std::fs::create_dir(&directory)?;
    let result = measure(&options, &directory.join("sounding.png"));
    // The process owns this new directory and only the renderer's PNG is disposable.
    let png = directory.join("sounding.png");
    if png.exists() {
        std::fs::remove_file(png)?;
    }
    std::fs::remove_dir(directory)?;
    let data = result?;
    // A failed run never leaves a partial result that could be mistaken for a baseline.
    let mut file = std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&options.output)?;
    use std::io::Write;
    writeln!(file, "{}", serde_json::to_string_pretty(&data)?)?;
    Ok(())
}
