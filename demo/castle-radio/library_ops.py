"""Demo-only waveform data and reversible library removal."""

import json
import shutil
from pathlib import Path

AUDIO_SUFFIXES = ("mp3", "opus", "wav", "flac")


def track_path(library, key):
    return next(
        (
            library / f"{key}.{suffix}"
            for suffix in AUDIO_SUFFIXES
            if (library / f"{key}.{suffix}").is_file()
        ),
        None,
    )


def known_key(root, catalog_path, key):
    """The library's own spelling of a song key: a catalog row or a media
    file. Paths are built from that, never from the request's string."""
    if not key or Path(key).name != key:
        raise ValueError("Unknown song")
    if catalog_path.exists():
        for row in json.loads(catalog_path.read_text()):
            if row.get("key") == key:
                return str(row["key"])
    for path in (root / "media").glob("*.mp3"):
        if path.name == key:
            return path.name
    raise ValueError("Unknown song")


def waveform(root, library, key):
    key = known_key(root, root / ".radio-data" / "catalog.json", key)
    split = library / "stems" / key / "analysis.json"
    if split.exists():
        return json.loads(split.read_text())
    if key.startswith("radio_"):
        source = track_path(library, key)
    else:
        source = root / "media" / key
    if source is None or not source.is_file():
        raise ValueError("Audio is not available")
    cache = root / ".radio-data" / "waveforms"
    cache.mkdir(exist_ok=True)
    target = cache / f"{key}.json"
    if target.exists() and target.stat().st_mtime >= source.stat().st_mtime:
        return json.loads(target.read_text())
    from stems import analyse_layers

    result = analyse_layers({"combined": source})
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(result))
    tmp.replace(target)
    return result


def write_catalog(path, rows):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(rows))
    temp.replace(path)


def remove(data, library, catalog_path, key):
    rows = json.loads(catalog_path.read_text())
    row = next((r for r in rows if r["key"] == key), None)
    if row is None:
        raise ValueError("This song is no longer in the library")
    key = str(row["key"])
    trash = data / "trash" / key
    if trash.exists():
        raise ValueError("Restore the previous removed copy before removing again")
    trash.mkdir(parents=True)
    candidates = [
        library / "stems" / key,
        data / f"{key}.yaml",
        data / "waveforms" / f"{key}.json",
    ]
    candidates += [
        library / f"{key}.{suffix}" for suffix in (*AUDIO_SUFFIXES, "cue", "show.json")
    ]
    named = row.get("playback_file")
    if named:
        candidates.append(library / Path(named).name)
    candidates += list((library / "_src").glob(key + ".*"))
    candidates += list(data.glob(key + ".*"))
    moved = []
    try:
        for path in dict.fromkeys(candidates):
            if path.exists():
                relative = path.relative_to(data)
                dest = trash / "files" / relative
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(path, dest)
                moved.append(str(relative))
        (trash / "record.json").write_text(json.dumps({"track": row, "paths": moved}))
        write_catalog(catalog_path, [r for r in rows if r["key"] != key])
    except Exception:
        for relative in moved:
            shutil.move(trash / "files" / relative, data / relative)
        shutil.rmtree(trash)
        raise
    return {"removed": key, "undo": True}


def restore(data, catalog_path, key):
    trash = next((d for d in (data / "trash").glob("radio_*") if d.name == key), None)
    if trash is None:
        raise ValueError("Unknown removed song")
    key = trash.name
    record = json.loads((trash / "record.json").read_text())
    rows = json.loads(catalog_path.read_text())
    if any(r["key"] == key for r in rows):
        raise ValueError("That song is already in the library")
    for relative in record["paths"]:
        path = data / relative
        if path.exists():
            raise ValueError("A newer copy exists; restore would overwrite it")
    for relative in record["paths"]:
        path = data / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(trash / "files" / relative, path)
    write_catalog(catalog_path, [*rows, record["track"]])
    shutil.rmtree(trash)
    return record["track"]
