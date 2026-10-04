"""The principal's own signed-in browser profiles (LOCUS-350, decision D-25).

The agent drives the principal's Chrome, Edge and Firefox through the Locus
WebExtension (``apps/browser-extension``) and a native-messaging host that
relays to the backend over loopback. See ``docs/COMPUTER-USE.md``.

* :mod:`.sites` -- registrable sites (eTLD+1)
* :mod:`.tiers` -- the principal-only browser autonomy tier and consent record
* :mod:`.pairing` -- pinned extension IDs and the pairing key (OS secret store)
* :mod:`.relay` -- the in-process command relay (pairing, panic, bounded queues)
* :mod:`.driver` -- :class:`UserBrowserDriver`, the ``"user"`` ``BrowserDriver``
* :mod:`.native_host` -- the native-messaging host process

Kept import-light: the gateway imports :mod:`.state` lazily.
"""
