//! Bounded member acquisition with stable output and fatal-error precedence.

use std::sync::{
    atomic::{AtomicUsize, Ordering},
    OnceLock,
};

use rayon::{prelude::*, ThreadPool, ThreadPoolBuilder};

pub(crate) fn pool() -> Result<&'static ThreadPool, String> {
    static POOL: OnceLock<Result<ThreadPool, String>> = OnceLock::new();
    POOL.get_or_init(|| {
        ThreadPoolBuilder::new()
            .num_threads(4)
            .thread_name(|index| format!("sharpmod-ensemble-{index}"))
            .build()
            .map_err(|error| error.to_string())
    })
    .as_ref()
    .map_err(Clone::clone)
}

pub(crate) fn acquire<T: Send, E: Send>(
    pool: &ThreadPool,
    count: usize,
    run: impl Fn(usize) -> Result<T, E> + Sync,
) -> Result<Vec<T>, E> {
    let stop = AtomicUsize::new(count);
    let results: Vec<_> = pool.install(|| {
        (0..count)
            .into_par_iter()
            .map(|index| {
                if index > stop.load(Ordering::Relaxed) {
                    return None;
                }
                let result = run(index);
                if result.is_err() {
                    stop.fetch_min(index, Ordering::Relaxed);
                }
                Some(result)
            })
            .collect()
    });
    // All started work has joined before the caller can remove its files.
    results.into_iter().flatten().collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Barrier;
    use std::time::Duration;

    #[test]
    fn members_overlap_with_a_four_worker_ceiling_and_keep_input_order() {
        let pool = ThreadPoolBuilder::new().num_threads(4).build().unwrap();
        let barrier = Barrier::new(4);
        let active = AtomicUsize::new(0);
        let peak = AtomicUsize::new(0);
        let output = acquire(&pool, 8, |index| {
            let running = active.fetch_add(1, Ordering::SeqCst) + 1;
            peak.fetch_max(running, Ordering::SeqCst);
            barrier.wait();
            active.fetch_sub(1, Ordering::SeqCst);
            Ok::<_, ()>(index)
        })
        .unwrap();
        assert_eq!(output, (0..8).collect::<Vec<_>>());
        assert_eq!(peak.load(Ordering::SeqCst), 4);
        assert_eq!(active.load(Ordering::SeqCst), 0);
    }

    #[test]
    fn earliest_requested_fatal_error_wins_after_running_members_join() {
        let pool = ThreadPoolBuilder::new().num_threads(4).build().unwrap();
        let active = AtomicUsize::new(0);
        let result = acquire(&pool, 32, |index| {
            active.fetch_add(1, Ordering::SeqCst);
            if index == 0 {
                std::thread::sleep(Duration::from_millis(10));
            }
            active.fetch_sub(1, Ordering::SeqCst);
            if index <= 1 {
                Err(index)
            } else {
                Ok(index)
            }
        });
        assert_eq!(result, Err(0));
        assert_eq!(active.load(Ordering::SeqCst), 0);
    }

    #[test]
    fn a_failed_member_can_remain_an_ordered_ledger_row() {
        let pool = ThreadPoolBuilder::new().num_threads(4).build().unwrap();
        let result = acquire(&pool, 5, |index| {
            Ok::<_, ()>(if index == 2 { None } else { Some(index) })
        })
        .unwrap();
        assert_eq!(result, vec![Some(0), Some(1), None, Some(3), Some(4)]);
    }
}
