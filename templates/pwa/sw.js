// Service worker mínimo: red primero para navegaciones y página offline de reserva.
// No cachea HTML ni parciales HTMX para no servir páginas obsoletas.
const CACHE = "picaporte-{{ APP_VERSION }}";
{% verbatim %}
const OFFLINE_URL = "/offline/";

self.addEventListener("install", (event) => {
  event.waitUntil(
    (async () => {
      const cache = await caches.open(CACHE);
      await cache.add(new Request(OFFLINE_URL, { cache: "reload" }));
    })()
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      if ("navigationPreload" in self.registration) {
        await self.registration.navigationPreload.enable();
      }
      const keys = await caches.keys();
      await Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key)));
    })()
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  if (event.request.mode !== "navigate") return;
  event.respondWith(
    (async () => {
      try {
        const preload = await event.preloadResponse;
        if (preload) return preload;
        return await fetch(event.request);
      } catch (error) {
        const cache = await caches.open(CACHE);
        return (await cache.match(OFFLINE_URL)) || Response.error();
      }
    })()
  );
});
{% endverbatim %}
