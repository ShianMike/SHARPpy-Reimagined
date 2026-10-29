use super::*;
use std::cell::RefCell;

fn request(object: &str) -> Request {
    Request {
        model: "hrrr".into(),
        product: "prs".into(),
        sources: ["aws", "google", "azure"]
            .map(|name| Source {
                name: name.into(),
                url: format!("https://{name}/{object}"),
            })
            .into(),
        reference: Some(format!("https://aws/{object}")),
        ttl: 10.,
    }
}

fn sample(size: u64, elapsed: f64) -> Option<Probe> {
    Some(Probe { size, elapsed })
}

#[test]
fn reference_size_excludes_a_faster_different_object_and_name_breaks_ties() {
    let request = request("a");
    assert_eq!(
        choose(
            &request.sources,
            &[sample(100, 0.4), sample(100, 0.2), sample(200, 0.1)],
            request.reference.as_deref()
        ),
        1
    );
    assert_eq!(
        choose(
            &request.sources,
            &[sample(100, 0.2); 3],
            request.reference.as_deref()
        ),
        0
    );
    assert_eq!(
        choose(
            &request.sources,
            &[None, None, None],
            Some(&request.sources[1].url)
        ),
        1
    );
}

#[test]
fn an_unavailable_reference_uses_the_modal_positive_size_then_the_larger_tie() {
    let request = request("a");
    assert_eq!(
        choose(
            &request.sources,
            &[None, sample(100, 0.2), sample(200, 0.3)],
            request.reference.as_deref()
        ),
        2
    );
    assert_eq!(
        choose(
            &request.sources,
            &[sample(0, 0.01), sample(100, 0.2), sample(100, 0.3)],
            None
        ),
        1
    );
    assert_eq!(
        choose(&request.sources, &[sample(0, 0.01), None, None], None),
        0
    );
}

#[test]
fn a_cached_mirror_is_revalidated_and_changed_sizes_or_failures_force_a_complete_race() {
    let router = Router::default();
    let mut request = request("a");
    let calls = RefCell::new(Vec::new());
    let probe = |sources: &[Source]| {
        calls.borrow_mut().push(sources.len());
        sources
            .iter()
            .map(|source| sample(100, if source.name == "aws" { 0.1 } else { 0.2 }))
            .collect()
    };
    assert_eq!(router.select(&request, 0., probe), 0);
    request.sources.reverse();
    assert_eq!(router.select(&request, 1., probe), 2);
    assert_eq!(*calls.borrow(), vec![3, 1]);
    request.reference = Some("https://google/a".into());
    let changed = |sources: &[Source]| {
        calls.borrow_mut().push(sources.len());
        sources
            .iter()
            .map(|source| sample(if source.name == "aws" { 200 } else { 100 }, 0.2))
            .collect()
    };
    assert_eq!(router.select(&request, 2., changed), 0);
    let failed = |sources: &[Source]| {
        calls.borrow_mut().push(sources.len());
        sources
            .iter()
            .map(|source| {
                (source.name != "azure").then_some(Probe {
                    size: 100,
                    elapsed: 0.2,
                })
            })
            .collect()
    };
    assert_eq!(router.select(&request, 3., failed), 2);
    assert_eq!(*calls.borrow(), vec![3, 1, 1, 3, 1, 3]);
}

#[test]
fn cache_identity_includes_model_product_and_all_exact_urls_and_expires_at_ttl() {
    let router = Router::default();
    let mut request = request("a");
    let calls = RefCell::new(Vec::new());
    let probe = |sources: &[Source]| {
        calls.borrow_mut().push(sources.len());
        vec![sample(100, 0.1); sources.len()]
    };
    router.select(&request, 0., probe);
    router.select(&request, 1., probe);
    router.select(&request, 10., probe);
    request.model = "gefs".into();
    router.select(&request, 11., probe);
    request.product = "atmos.5".into();
    router.select(&request, 12., probe);
    request.sources[2].url.push_str("-new-object");
    router.select(&request, 13., probe);
    assert_eq!(*calls.borrow(), vec![3, 1, 3, 3, 3, 3]);
}

#[test]
fn unsuccessful_probes_fall_back_then_are_retried_without_caching_false_success() {
    let router = Router::default();
    let request = request("a");
    assert_eq!(
        router.select(&request, 0., |sources| vec![None; sources.len()]),
        0
    );
    let counts = RefCell::new(Vec::new());
    router.select(&request, 1., |sources| {
        counts.borrow_mut().push(sources.len());
        vec![None; sources.len()]
    });
    assert_eq!(*counts.borrow(), vec![1, 3]);
}

#[test]
fn cache_stays_bounded_and_removes_the_earliest_expiry() {
    let router = Router::default();
    for index in 0..257 {
        let mut request = request(&index.to_string());
        request.ttl = if index == 0 { 1. } else { 10. };
        router.select(&request, 0., |sources| {
            vec![sample(100, 0.1); sources.len()]
        });
    }
    let cache = router.cache.lock().unwrap();
    assert_eq!(cache.len(), 256);
    assert!(cache.values().all(|value| value.expires == 10.));
}
