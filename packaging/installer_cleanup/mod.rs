//! Remove only obsolete bundled runtime paths, with a legacy code-file fallback.
use std::{
    collections::BTreeSet,
    fs,
    io::{self, Read, Write},
    path::{Component, Path, PathBuf},
    thread,
    time::Duration,
};

const HEADER: &str = "io.github.shianmike.sharppy-reimagined/runtime-files/v1";
const PREVIOUS_HEADER: &str = "io.github.shianmike.sharppy-reimagined/previous-runtime-files/v1";
const INVENTORY: &str = ".sharppy-runtime-files";
const MAX_FILES: usize = 100_000;

struct Inventory {
    version: String,
    files: BTreeSet<String>,
}

fn invalid(message: &str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message)
}

fn ordinary(path: &Path) -> io::Result<fs::Metadata> {
    let metadata = fs::symlink_metadata(path)?;
    #[cfg(windows)]
    let reparse = {
        use std::os::windows::fs::MetadataExt;
        metadata.file_attributes() & 0x400 != 0
    };
    #[cfg(not(windows))]
    let reparse = metadata.file_type().is_symlink();
    if reparse || !(metadata.is_file() || metadata.is_dir()) {
        return Err(invalid(
            "runtime cleanup refuses links, junctions and special files",
        ));
    }
    Ok(metadata)
}

fn root(path: &Path) -> io::Result<PathBuf> {
    if !path.is_absolute()
        || path
            .components()
            .any(|part| matches!(part, Component::ParentDir))
    {
        return Err(invalid("installer directory must be an absolute path"));
    }
    for ancestor in path.ancestors() {
        if !ordinary(ancestor)?.is_dir() {
            return Err(invalid("invalid installer directory"));
        }
    }
    let resolved = fs::canonicalize(path)?;
    if resolved.parent().is_none() {
        return Err(invalid("a filesystem root is not an install directory"));
    }
    Ok(resolved)
}

fn relative(path: &str) -> io::Result<&str> {
    if path.is_empty()
        || path.contains(['\\', ':', '\0', '\r', '\n'])
        || path
            .split('/')
            .any(|part| part.is_empty() || part == "." || part == "..")
    {
        return Err(invalid("invalid runtime inventory path"));
    }
    Ok(path)
}

pub fn prepare(
    installation: &Path,
    previous_inventory: &Path,
    registered: &Path,
) -> io::Result<()> {
    if !installation.is_absolute()
        || installation
            .components()
            .any(|part| matches!(part, Component::ParentDir))
    {
        return Err(invalid("installer directory must be an absolute path"));
    }
    // Check parents before creating a fresh directory or allowing NSIS to copy
    // files: a linked old runtime must be rejected before the new install.
    for ancestor in installation.ancestors() {
        match fs::symlink_metadata(ancestor) {
            Ok(_) => {
                ordinary(ancestor)?;
            }
            Err(error) if error.kind() == io::ErrorKind::NotFound => {}
            Err(error) => return Err(error),
        }
    }
    fs::create_dir_all(installation)?;
    let installation = root(installation)?;
    let backend = installation.join("backend");
    let mut entries = Vec::new();
    match fs::symlink_metadata(&backend) {
        Ok(_) => {
            if !ordinary(&backend)?.is_dir() {
                return Err(invalid("bundled backend path is not a directory"));
            }
            files(&backend, "", 0, &mut entries, &mut Vec::new())?;
        }
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(()),
        Err(error) => return Err(error),
    }
    // The stock installer records its previous root before this install writes
    // anything. A new destination may contain another application's backend.
    if !root(registered).is_ok_and(|registered| registered == installation) {
        return Ok(());
    }
    let previous = match read_inventory(&backend.join(INVENTORY)) {
        Ok(inventory) => inventory.files,
        Err(error) if error.kind() == io::ErrorKind::NotFound => entries
            .into_iter()
            .map(|(name, _)| key(&name))
            .filter(|name| legacy_code(name))
            .collect(),
        Err(error) => return Err(error),
    };
    let mut output = fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(previous_inventory)?;
    writeln!(output, "{PREVIOUS_HEADER}")?;
    for name in previous {
        writeln!(output, "{name}")?;
    }
    Ok(())
}

fn key(path: &str) -> String {
    path.to_ascii_lowercase()
}

fn read_record(path: &Path) -> io::Result<String> {
    if !ordinary(path)?.is_file() {
        return Err(invalid("runtime inventory is not a file"));
    }
    let mut source = String::new();
    fs::File::open(path)?
        .take(8 * 1024 * 1024 + 1)
        .read_to_string(&mut source)?;
    if source.len() > 8 * 1024 * 1024 {
        return Err(invalid("runtime inventory is too large"));
    }
    Ok(source)
}

fn read_inventory(path: &Path) -> io::Result<Inventory> {
    let source = read_record(path)?;
    let mut lines = source.lines();
    if lines.next() != Some(HEADER) {
        return Err(invalid("unrecognized runtime inventory"));
    }
    let version = lines
        .next()
        .ok_or_else(|| invalid("missing runtime version"))?;
    if version.split('.').count() != 3
        || version
            .split('.')
            .any(|part| part.is_empty() || !part.bytes().all(|byte| byte.is_ascii_digit()))
    {
        return Err(invalid("invalid runtime version"));
    }
    let files = read_names(lines)?;
    if !files.contains("sharppy-backend.exe")
        || !files.iter().any(|name| name.starts_with("_internal/"))
    {
        return Err(invalid("runtime inventory lacks the bundled application"));
    }
    Ok(Inventory {
        version: version.into(),
        files,
    })
}

fn read_names<'a>(lines: impl Iterator<Item = &'a str>) -> io::Result<BTreeSet<String>> {
    let mut files = BTreeSet::new();
    for line in lines {
        files.insert(key(relative(line)?));
        if files.len() > MAX_FILES {
            return Err(invalid("runtime inventory has too many files"));
        }
    }
    Ok(files)
}

fn files(
    directory: &Path,
    prefix: &str,
    depth: usize,
    output: &mut Vec<(String, PathBuf)>,
    directories: &mut Vec<PathBuf>,
) -> io::Result<()> {
    if depth > 32 {
        return Err(invalid("runtime directory nesting is excessive"));
    }
    for entry in fs::read_dir(directory)? {
        let entry = entry?;
        let path = entry.path();
        let metadata = ordinary(&path)?;
        let name = entry
            .file_name()
            .into_string()
            .map_err(|_| invalid("invalid runtime filename"))?;
        let name = if prefix.is_empty() {
            name
        } else {
            format!("{prefix}/{name}")
        };
        relative(&name)?;
        if metadata.is_dir() {
            files(&path, &name, depth + 1, output, directories)?;
            directories.push(path);
        } else {
            output.push((name, path));
        }
        if output.len() + directories.len() > MAX_FILES {
            return Err(invalid("runtime has too many entries"));
        }
    }
    Ok(())
}

pub fn write_inventory(directory: &Path, version: &str) -> io::Result<()> {
    let directory = root(directory)?;
    let mut entries = Vec::new();
    files(&directory, "", 0, &mut entries, &mut Vec::new())?;
    let mut names: BTreeSet<_> = entries.into_iter().map(|(name, _)| key(&name)).collect();
    names.insert(INVENTORY.into());
    let content = format!(
        "{HEADER}\n{version}\n{}\n",
        names.into_iter().collect::<Vec<_>>().join("\n")
    );
    let destination = directory.join(INVENTORY);
    fs::write(&destination, content)?;
    read_inventory(&destination)?;
    Ok(())
}

fn legacy_code(name: &str) -> bool {
    // Before v3.8.4 there was no installed inventory. Restrict that fallback
    // to bundled code and package metadata, preserving unrelated data files.
    let metadata = name.split('/').any(|part| {
        (part.ends_with(".dist-info") || part.ends_with(".egg-info"))
            && part
                .split('-')
                .any(|part| part.starts_with(|character: char| character.is_ascii_digit()))
    });
    let code = Path::new(name)
        .extension()
        .and_then(|value| value.to_str())
        .is_some_and(|extension| {
            matches!(
                extension,
                "py" | "pyc" | "pyo" | "pyd" | "dll" | "so" | "whl"
            )
        });
    name.starts_with("_internal/") && (metadata || code)
        || !name.contains('/') && name.ends_with(".whl") && name.starts_with("sharpmod")
}

pub fn prune(installation: &Path, previous_inventory: &Path) -> io::Result<()> {
    let installation = root(installation)?;
    let backend = installation.join("backend");
    if !ordinary(&backend)?.is_dir() {
        return Err(invalid("bundled backend directory is missing"));
    }
    let current = read_inventory(&backend.join(INVENTORY))?;
    let report_path = installation.join("installer-cleanup.json");
    match fs::symlink_metadata(&report_path) {
        Ok(_) => {
            if !ordinary(&report_path)?.is_file() {
                return Err(invalid("cleanup report is not a file"));
            }
        }
        Err(error) if error.kind() == io::ErrorKind::NotFound => {}
        Err(error) => return Err(error),
    }
    let previous = match read_record(previous_inventory) {
        Ok(source) => {
            let mut lines = source.lines();
            if lines.next() != Some(PREVIOUS_HEADER) {
                return Err(invalid("unrecognized previous runtime inventory"));
            }
            read_names(lines)?
        }
        Err(error) if error.kind() == io::ErrorKind::NotFound => BTreeSet::new(),
        Err(error) => return Err(error),
    };
    let mut entries = Vec::new();
    let mut directories = Vec::new();
    // Complete validation precedes any deletion. Traversal rejects every
    // reparse point and never follows an unexpected descendant outside backend.
    files(&backend, "", 0, &mut entries, &mut directories)?;
    let present: BTreeSet<_> = entries.iter().map(|(name, _)| key(name)).collect();
    if current.files.iter().any(|name| !present.contains(name)) {
        return Err(invalid(
            "new runtime is incomplete; obsolete files were preserved",
        ));
    }
    let obsolete: Vec<_> = entries
        .into_iter()
        .filter(|(name, _)| {
            let name = key(name);
            previous.contains(&name) && !current.files.contains(&name)
        })
        .collect();
    let mut empty_candidates = BTreeSet::new();
    for (_, path) in &obsolete {
        for parent in path
            .ancestors()
            .skip(1)
            .take_while(|parent| *parent != backend)
        {
            empty_candidates.insert(parent.to_path_buf());
        }
        for attempt in 0..5 {
            ordinary(path)?;
            match fs::remove_file(path) {
                Ok(()) => break,
                Err(error) if attempt == 4 => return Err(error),
                Err(_) => thread::sleep(Duration::from_millis(200)),
            }
        }
    }
    let mut removed_directories = 0;
    for directory in directories {
        ordinary(&directory)?;
        if empty_candidates.contains(&directory) && fs::read_dir(&directory)?.next().is_none() {
            fs::remove_dir(&directory)?;
            removed_directories += 1;
        }
    }
    let report=format!("{{\"ok\":true,\"version\":\"{}\",\"runtime_files\":{},\"removed_files\":{},\"removed_directories\":{removed_directories}}}\n",
        current.version,current.files.len(),obsolete.len());
    fs::write(report_path, report)
}

#[cfg(test)]
mod tests;
