//! Regression for native PyO3 imports that PyInstaller cannot discover itself.
#[test]
fn winter_svg_modules_are_bundled_and_real_views_are_required() {
    let spec = include_str!("../../packaging/sharpmod_backend.spec");
    for module in [
        "qtpy.QtSvg",
        "PySide6.QtSvg",
        "qtpy.QtSvgWidgets",
        "PySide6.QtSvgWidgets",
    ] {
        assert!(
            spec.contains(&format!("\"{module}\"")),
            "frozen backend omits {module}"
        );
    }
    let workflow = include_str!("../../.github/workflows/release.yml");
    assert!(workflow.contains("-ExpectedVersion $version -RequireWinter"));
    let verifier = include_str!("../../packaging/verify_desktop_artifact.ps1");
    for name in ["severe_hd", "winter_hd", "winter_uhd"] {
        assert!(
            verifier.contains(name),
            "packaged view missing from release gate: {name}"
        );
    }
}
