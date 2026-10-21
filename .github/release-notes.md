Riff 0.6.28 improves browsing large music libraries on mobile.

The library starts with a small batch of recordings and adds more as you scroll. Search, genre tags, sorting, favorites and archived recordings continue to cover the entire collection. Take history also loads in batches, and cover artwork paints across idle turns so it does not block the whole screen at once.

State polling now sends only changing runtime information when the catalog is unchanged. Cached catalog snapshots, genre matching and compressed JSON reduce server work and transfer size. Database revision tracking catches changes made by other studio processes. Existing API clients can continue requesting complete snapshots.

Validation includes a 1,200-recording mobile regression and controlled measurements against a 1,717-recording library. These are browser/server measurements, not a guarantee of speed on every phone. The first catalog load still scales with collection size.

The graphical macOS download remains an ad-hoc signed preview with no Developer ID signature or notarization. NVIDIA desktop packaging remains in development. The ChatGPT plugin is a separate downstream delivery.
