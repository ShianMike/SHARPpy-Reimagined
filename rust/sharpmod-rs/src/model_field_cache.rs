//! Bounded immutable source fields, with independent owners for shared work.
use std::{
    collections::{HashMap, VecDeque},
    hash::Hash,
    sync::{
        atomic::{AtomicUsize, Ordering},
        Arc, Condvar, Mutex,
    },
    time::Duration,
};

struct Flight<V> {
    value: Mutex<Option<Result<Arc<V>, WaitError>>>,
    changed: Condvar,
    users: AtomicUsize,
}

impl<V> Flight<V> {
    fn try_join_transient(&self) -> bool {
        let mut users = self.users.load(Ordering::Relaxed);
        // One remaining user is the producer. A new consumer needs a new flight.
        while users > 1 {
            match self.users.compare_exchange_weak(
                users,
                users + 1,
                Ordering::Relaxed,
                Ordering::Relaxed,
            ) {
                Ok(_) => return true,
                Err(current) => users = current,
            }
        }
        false
    }
}

struct State<K, V> {
    entries: HashMap<K, (Arc<Flight<V>>, usize)>,
    order: VecDeque<K>,
    bytes: usize,
}

pub(crate) struct Cache<K, V> {
    state: Mutex<State<K, V>>,
    budget: usize,
    capacity: usize,
}

pub(crate) struct Ticket<K: Eq + Hash + Clone, V> {
    cache: Arc<Cache<K, V>>,
    key: Option<K>,
    flight: Arc<Flight<V>>,
    pub leader: bool,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) enum WaitError {
    Cancelled,
    Abandoned,
    Failed(String),
}

impl<K: Eq + Hash + Clone, V> Cache<K, V> {
    pub fn new(budget: usize, capacity: usize) -> Arc<Self> {
        Arc::new(Self {
            state: Mutex::new(State {
                entries: HashMap::new(),
                order: VecDeque::new(),
                bytes: 0,
            }),
            budget,
            capacity,
        })
    }

    pub fn reserve(self: &Arc<Self>, key: Option<K>) -> Ticket<K, V> {
        self.reserve_with(key, false)
    }

    /// Reserve a consumer ticket and, for a new flight, a separate producer.
    pub fn reserve_transient(self: &Arc<Self>, key: K) -> (Ticket<K, V>, Option<Ticket<K, V>>) {
        let ticket = self.reserve_with(Some(key), true);
        if ticket.leader {
            let consumer = Ticket {
                cache: Arc::clone(self),
                key: None,
                flight: Arc::clone(&ticket.flight),
                leader: false,
            };
            (consumer, Some(ticket))
        } else {
            (ticket, None)
        }
    }

    fn reserve_with(self: &Arc<Self>, key: Option<K>, transient: bool) -> Ticket<K, V> {
        let mut state = self.state.lock().unwrap();
        if let Some(key) = &key {
            if let Some((flight, bytes)) = state.entries.get(key) {
                let joined = if transient {
                    flight.try_join_transient()
                } else {
                    flight.users.fetch_add(1, Ordering::Relaxed);
                    true
                };
                if joined {
                    let flight = Arc::clone(flight);
                    state.order.retain(|item| item != key);
                    state.order.push_back(key.clone());
                    return Ticket {
                        cache: Arc::clone(self),
                        key: Some(key.clone()),
                        flight,
                        leader: false,
                    };
                }
                // The previous request left its producer with no consumers.
                let bytes = *bytes;
                state.entries.remove(key);
                state.order.retain(|item| item != key);
                state.bytes -= bytes;
            }
        }
        let flight = Arc::new(Flight {
            value: Mutex::new(None),
            changed: Condvar::new(),
            users: AtomicUsize::new(if transient { 2 } else { 1 }),
        });
        if key.is_some() && state.entries.len() >= self.capacity {
            if let Some(position) = state
                .order
                .iter()
                .position(|key| state.entries[key].0.value.lock().unwrap().is_some())
            {
                let old = state.order.remove(position).unwrap();
                if let Some((_, bytes)) = state.entries.remove(&old) {
                    state.bytes -= bytes;
                }
            }
        }
        let key = key.filter(|_| state.entries.len() < self.capacity);
        if let Some(key) = &key {
            state.entries.insert(key.clone(), (Arc::clone(&flight), 0));
            state.order.push_back(key.clone());
        }
        Ticket {
            cache: Arc::clone(self),
            key,
            flight,
            leader: true,
        }
    }

    pub fn clear(&self) {
        let mut state = self.state.lock().unwrap();
        state.entries.clear();
        state.order.clear();
        state.bytes = 0;
    }

    fn finish(&self, key: &K, flight: &Arc<Flight<V>>, bytes: usize, success: bool) {
        let mut state = self.state.lock().unwrap();
        if !state
            .entries
            .get(key)
            .is_some_and(|(current, _)| Arc::ptr_eq(current, flight))
        {
            return; // An explicit clear must not be undone by an older producer.
        }
        if !success || bytes > self.budget {
            state.entries.remove(key);
            state.order.retain(|item| item != key);
            return;
        }
        state.entries.get_mut(key).unwrap().1 = bytes;
        state.bytes += bytes;
        while state.bytes > self.budget {
            let Some(position) = state.order.iter().position(|key| state.entries[key].1 > 0) else {
                break;
            };
            let key = state.order.remove(position).unwrap();
            if let Some((_, bytes)) = state.entries.remove(&key) {
                state.bytes -= bytes;
            }
        }
    }
}

impl<K: Eq + Hash + Clone, V> Ticket<K, V> {
    pub fn shared(&self) -> bool {
        self.flight.users.load(Ordering::Relaxed) > 1
    }

    pub fn complete(&self, value: Result<V, String>, bytes: usize) {
        self.finish(value.map(Arc::new).map_err(WaitError::Failed), Some(bytes));
    }

    /// Deliver a result only to callers that already hold this flight.
    pub fn complete_transient(self, value: V) {
        // Retire the key before a consumer can observe the result and reserve it again.
        if let Some(key) = &self.key {
            self.cache.finish(key, &self.flight, 0, false);
        }
        self.finish(Ok(Arc::new(value)), None);
    }

    fn finish(&self, value: Result<Arc<V>, WaitError>, bytes: Option<usize>) {
        let success = value.is_ok();
        let mut stored = self.flight.value.lock().unwrap();
        if stored.is_some() {
            return;
        }
        *stored = Some(value);
        drop(stored);
        if let (Some(key), Some(bytes)) = (&self.key, bytes) {
            self.cache.finish(key, &self.flight, bytes, success);
        }
        self.flight.changed.notify_all();
    }

    pub fn wait(&self, cancelled: impl Fn() -> Result<bool, String>) -> Result<Arc<V>, WaitError> {
        loop {
            // A Python callback can acquire the GIL. Never call it while holding
            // a cache lock that a producer attached to Python may need.
            if cancelled().map_err(WaitError::Failed)? {
                return Err(WaitError::Cancelled);
            }
            let value = self.flight.value.lock().unwrap();
            if let Some(result) = &*value {
                return result.clone();
            }
            drop(
                self.flight
                    .changed
                    .wait_timeout(value, Duration::from_millis(50))
                    .unwrap(),
            );
        }
    }
}

impl<K: Eq + Hash + Clone, V> Drop for Ticket<K, V> {
    fn drop(&mut self) {
        if self.leader {
            self.finish(Err(WaitError::Abandoned), Some(0));
        }
        self.flight.users.fetch_sub(1, Ordering::Relaxed);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn transient_flights_retire_when_consumers_leave_or_the_result_is_ready() {
        let cache = Cache::<u8, u8>::new(0, 1);
        let (consumer, producer) = cache.reserve_transient(1);
        let producer = producer.unwrap();
        drop(consumer);
        assert!(!producer.shared());

        let (consumer, replacement) = cache.reserve_transient(1);
        let replacement = replacement.unwrap();
        producer.complete_transient(7);
        let (follower, new_producer) = cache.reserve_transient(1);
        assert!(new_producer.is_none());
        replacement.complete_transient(8);
        assert_eq!(*consumer.wait(|| Ok(false)).unwrap(), 8);
        assert_eq!(*follower.wait(|| Ok(false)).unwrap(), 8);
        assert!(cache.reserve_transient(1).1.is_some());
    }

    #[test]
    fn shares_fields_preserves_independent_cancellation_and_retries_failed_producers() {
        let cache = Cache::<u8, Vec<f32>>::new(16, 8);
        let owner = cache.reserve(Some(1));
        let follower = cache.reserve(Some(1));
        assert!(owner.leader && !follower.leader && owner.shared());
        assert!(follower.wait(|| Ok(true)).is_err());
        owner.complete(Ok(vec![1., 2.]), 8);
        assert_eq!(&*follower.wait(|| Ok(false)).unwrap(), &[1., 2.]);
        drop(owner);
        assert!(!cache.reserve(Some(1)).leader);
        let failed = cache.reserve(Some(2));
        drop(failed);
        assert!(cache.reserve(Some(2)).leader);
    }

    #[test]
    fn evicts_by_bytes_and_clear_does_not_retain_an_old_inflight_result() {
        let cache = Cache::<u8, Vec<f32>>::new(8, 8);
        cache.reserve(Some(1)).complete(Ok(vec![1., 2.]), 8);
        cache.reserve(Some(2)).complete(Ok(vec![3., 4.]), 8);
        assert!(cache.reserve(Some(1)).leader);
        let old = cache.reserve(Some(3));
        cache.clear();
        old.complete(Ok(vec![5.]), 4);
        assert!(cache.reserve(Some(3)).leader);
        assert_eq!(cache.state.lock().unwrap().bytes, 0);
    }

    #[test]
    fn concurrent_consumers_wake_after_success_without_sharing_mutable_output() {
        let cache = Cache::<u8, Vec<f32>>::new(32, 8);
        let owner = cache.reserve(Some(1));
        std::thread::scope(|scope| {
            let follower = cache.reserve(Some(1));
            let thread = scope.spawn(move || follower.wait(|| Ok(false)).unwrap());
            owner.complete(Ok(vec![7.]), 4);
            assert_eq!(&*thread.join().unwrap(), &[7.]);
        });
    }

    #[test]
    fn metadata_capacity_and_oversized_values_do_not_block_existing_consumers() {
        let cache = Cache::<u8, Vec<f32>>::new(8, 1);
        let first = cache.reserve(Some(1));
        let follower = cache.reserve(Some(1));
        let bypass = cache.reserve(Some(2));
        bypass.complete(Ok(vec![3.]), 4);
        assert_eq!(cache.state.lock().unwrap().entries.len(), 1);
        first.complete(Ok(vec![1., 2., 3.]), 12);
        assert_eq!(&*follower.wait(|| Ok(false)).unwrap(), &[1., 2., 3.]);
        assert_eq!(cache.state.lock().unwrap().bytes, 0);
        assert!(cache.reserve(Some(1)).leader);
    }

    #[test]
    fn consumer_callback_can_finish_a_producer_without_lock_inversion() {
        let cache = Cache::<u8, u8>::new(8, 1);
        let owner = cache.reserve(Some(1));
        let follower = cache.reserve(Some(1));
        assert_eq!(
            *follower
                .wait(|| {
                    owner.complete(Ok(7), 1);
                    Ok(false)
                })
                .unwrap(),
            7
        );
        assert_eq!(follower.wait(|| Ok(true)), Err(WaitError::Cancelled));
    }
}
