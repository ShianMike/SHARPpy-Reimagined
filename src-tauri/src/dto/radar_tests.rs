use super::*;
use serde_json::json;

#[test]
fn legacy_catalogue_keeps_its_public_contract() {
    let catalog: RadarSiteCatalog = serde_json::from_str(include_str!(
        "../../contracts/bridge/map/radar-sites.result.json"
    ))
    .unwrap();
    catalog.validate().unwrap();
    let expected: serde_json::Value =
        serde_json::from_str(include_str!("../../contracts/public/map/radar-sites.json")).unwrap();
    assert_eq!(
        serde_json::to_value(catalog.into_public()).unwrap(),
        expected
    );
}

#[test]
fn opera_identity_survives_the_public_catalogue() {
    let catalog: RadarSiteCatalog = serde_json::from_value(json!({"sites": [{
        "id": "0-578-0-nohur", "lat": 59.627083, "lon": 10.564472,
        "name": "Hurum", "country": "NO", "provider": "OPERA", "products": ["reflectivity"]
    }]}))
    .unwrap();
    catalog.validate().unwrap();
    let result = serde_json::to_value(catalog.into_public()).unwrap();
    assert_eq!(result["sites"][0]["provider"], "OPERA");
    assert_eq!(result["sites"][0]["id"], "0-578-0-nohur");
    let mrrl = sharpmod_weather::radar_sites::sites()
        .iter()
        .find(|site| site.id == "MF90")
        .unwrap();
    assert_eq!(
        serde_json::to_value(RadarSite::from(mrrl)).unwrap(),
        json!({
            "id": "MF90",
            "latitude": 16.31444,
            "longitude": -61.34889,
            "name": "Le Moule",
            "country": "GP",
            "provider": "MRRL",
            "products": ["reflectivity"]
        })
    );
}

#[test]
fn australian_metadata_survives_both_catalogue_paths() {
    let native = sharpmod_weather::radar_sites::sites()
        .iter()
        .find(|site| site.id == "ABRD")
        .unwrap();
    let expected = json!({
        "id": "ABRD", "latitude": -37.691, "longitude": 144.946,
        "name": "Broadmeadows", "country": "AU", "products": ["reflectivity", "velocity"]
    });
    assert_eq!(
        serde_json::to_value(RadarSite::from(native)).unwrap(),
        expected
    );
    let catalog: RadarSiteCatalog = serde_json::from_value(json!({"sites": [{
        "id": "ABRD", "lat": -37.691, "lon": 144.946,
        "name": "Broadmeadows", "country": "AU", "products": ["reflectivity", "velocity"]
    }]}))
    .unwrap();
    catalog.validate().unwrap();
    assert_eq!(
        serde_json::to_value(catalog.into_public()).unwrap(),
        json!({"sites": [expected]})
    );
    let us = sharpmod_weather::radar_sites::sites()
        .iter()
        .find(|site| site.id == "KTLX")
        .unwrap();
    let public_us = serde_json::to_value(RadarSite::from(us)).unwrap();
    assert_eq!(public_us["name"], "Oklahoma City");
    assert!(public_us.get("products").is_none());
    let legacy: RadarSiteCatalog = serde_json::from_value(json!({"sites": [{
        "id": "KTLX", "lat": us.lat, "lon": us.lon,
        "products": ["reflectivity"]
    }]}))
    .unwrap();
    legacy.validate().unwrap();
    assert_eq!(
        serde_json::to_value(legacy.into_public()).unwrap(),
        json!({"sites": [{"id": "KTLX", "latitude": us.lat, "longitude": us.lon}]})
    );
}

#[test]
fn german_capabilities_survive_both_catalogue_paths() {
    let native = sharpmod_weather::radar_sites::sites()
        .iter()
        .find(|site| site.id == "DESS")
        .unwrap();
    let expected = json!({
        "id": "DESS", "latitude": native.lat, "longitude": native.lon,
        "name": "Essen", "country": "DE", "products": ["reflectivity"]
    });
    assert_eq!(
        serde_json::to_value(RadarSite::from(native)).unwrap(),
        expected
    );
    let catalog: RadarSiteCatalog = serde_json::from_value(json!({"sites": [{
        "id": "DESS", "lat": native.lat, "lon": native.lon,
        "name": "Essen", "country": "DE", "products": ["reflectivity"]
    }]}))
    .unwrap();
    catalog.validate().unwrap();
    assert_eq!(
        serde_json::to_value(catalog.into_public()).unwrap(),
        json!({"sites": [expected]})
    );
}

#[test]
fn catalogue_rejects_invalid_metadata_at_the_bridge_boundary() {
    for metadata in [
        json!({"name": ""}),
        json!({"country": "Australia"}),
        json!({"country": "au"}),
        json!({"products": []}),
        json!({"products": ["sr_bref"]}),
        json!({"products": ["reflectivity", "reflectivity"]}),
    ] {
        let mut site = json!({"id": "ABRD", "lat": -37.691, "lon": 144.946});
        site.as_object_mut()
            .unwrap()
            .extend(metadata.as_object().unwrap().clone());
        let catalog: RadarSiteCatalog = serde_json::from_value(json!({"sites": [site]})).unwrap();
        assert!(catalog.validate().is_err(), "{metadata}");
    }
}
