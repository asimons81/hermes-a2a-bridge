from __future__ import annotations

from hermes_a2a_bridge.registry_cache import RegistryProfileCache


def test_registry_profile_cache_expires_and_invalidates():
    now = [100.0]
    cache = RegistryProfileCache(clock=lambda: now[0])
    cache.put("demo", "http://registry.test", "http://agent.test")

    assert cache.get("demo", "http://registry.test", ttl_seconds=30) == "http://agent.test"
    now[0] += 31
    assert cache.get("demo", "http://registry.test", ttl_seconds=30) is None

    cache.put("demo", "http://registry.test", "http://agent.test")
    cache.invalidate("demo")
    assert cache.get("demo", "http://registry.test", ttl_seconds=30) is None


def test_registry_profile_cache_rejects_changed_registry_url():
    cache = RegistryProfileCache()
    cache.put("demo", "http://old-registry.test", "http://agent.test")

    assert cache.get("demo", "http://new-registry.test", ttl_seconds=60) is None
