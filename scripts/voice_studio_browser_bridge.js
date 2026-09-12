// Test transport only. URLs are dispatched to a fixed loopback fixture by Python.
// No browser policies are disabled. Production code never imports this file.
(() => {
  const abortError = () => new DOMException('Operation aborted', 'AbortError');
  const encode = bytes => { let s = ''; for (const n of bytes) s += String.fromCharCode(n); return btoa(s); };
  const decode = value => Uint8Array.from(atob(value), c => c.charCodeAt(0));
  window.fetch = async (url, init = {}) => {
    if (init.signal?.aborted) throw abortError();
    const headers = Object.fromEntries(new Headers(init.headers).entries());
    const request = {path: new URL(url, 'http://ux.fixture').pathname, method: init.method || 'GET', headers};
    if (init.body instanceof FormData) {
      request.form = [];
      for (const [key, value] of init.body.entries()) {
        request.form.push(typeof value === 'string' ? {key, value} : {
          key, file: encode(new Uint8Array(await value.arrayBuffer())), type: value.type, name: value.name,
        });
      }
    } else if (typeof init.body === 'string') request.body = init.body;
    const execute = window.__ux_http(request).then(result => {
      if (init.signal?.aborted) throw abortError();
      return new Response(result.status === 204 ? null : decode(result.body), {status: result.status, headers: result.headers});
    });
    if (!init.signal) return execute;
    return new Promise((resolve, reject) => {
      const cancel = () => reject(abortError());
      init.signal.addEventListener('abort', cancel, {once: true});
      execute.then(resolve, reject).finally(() => init.signal.removeEventListener('abort', cancel));
    });
  };
  let next = 0; const sockets = new Map();
  class Socket extends EventTarget {
    static CONNECTING = 0; static OPEN = 1; static CLOSING = 2; static CLOSED = 3;
    readyState = 0; bufferedAmount = 0;
    constructor(url) {
      super(); this.id = ++next; this.url = url; sockets.set(this.id, this);
      void window.__ux_ws_open({id: this.id, path: new URL(url).pathname});
    }
    send(text) { void window.__ux_ws_send({id: this.id, text}); }
    close() { this.readyState = 3; void window.__ux_ws_close(this.id); sockets.delete(this.id); }
    event(type, data) {
      if (this.readyState === 3) return;
      if (type === 'open') this.readyState = 1;
      if (type === 'close') this.readyState = 3;
      const event = type === 'message' ? new MessageEvent(type, {data}) : new Event(type);
      this.dispatchEvent(event); this['on' + type]?.(event);
    }
  }
  window.__ux_socket_event = ({id, type, data}) => sockets.get(id)?.event(type, data);
  window.WebSocket = Socket;
  window.__ux_microphone = {mode: 'synthetic', calls: 0, stops: 0, contexts: [], pending: []};
  Object.defineProperty(navigator, 'mediaDevices', {configurable: true, value: {
    getUserMedia: async () => {
      const fixture = window.__ux_microphone; fixture.calls++;
      if (fixture.mode === 'denied') throw new DOMException('Fixture denied', 'NotAllowedError');
      if (fixture.mode === 'pending') await new Promise(resolve => fixture.pending.push(resolve));
      const context = new AudioContext(); const oscillator = context.createOscillator();
      const gain = context.createGain(); gain.gain.value = 0.18; oscillator.frequency.value = 440;
      const destination = context.createMediaStreamDestination();
      oscillator.connect(gain); gain.connect(destination); oscillator.start(); await context.resume();
      fixture.contexts.push(context);
      destination.stream.getTracks().forEach(track => {
        const stop = track.stop.bind(track);
        track.stop = () => { fixture.stops++; stop(); void context.close(); };
      });
      return destination.stream;
    },
  }});
})();
