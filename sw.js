// Retired: the DTF Club hub (/sw.js, scope "/") now owns the service worker. This stub removes the old one.
self.addEventListener('install',()=>self.skipWaiting());self.addEventListener('activate',()=>self.registration.unregister());
