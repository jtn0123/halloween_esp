fn main() {
    // release.rs names this build's castle-core asset by its target triple.
    let target = std::env::var("TARGET").expect("cargo sets TARGET for build scripts");
    println!("cargo:rustc-env=CASTLE_TARGET={target}");
    tauri_build::build()
}
