// AIERP planner screen — sign-in.
//
// On load, Auth.bootstrap() reads GET /api/auth/config and then:
//
//   * development mode — a centred card with a labelled token field stores
//     the entered token in sessionStorage;
//   * production mode without a token — a redirect to the IdP's
//     /protocol/openid-connect/auth endpoint with a PKCE code challenge
//     (verifier and state kept in sessionStorage). The challenge is
//     base64url(SHA-256(verifier)) without padding, via crypto.subtle or,
//     in a non-secure context where crypto.subtle is absent, the
//     self-contained fallback below — no step of the flow needs a secure
//     context. On return, a matching code + state is exchanged at
//     /protocol/openid-connect/token and the access token stored in
//     sessionStorage; a mismatched state is refused with a visible error.
//
// The URL fragment is only ever a view route (#/overview, #/queue, #/log,
// each with an optional query string) — it is never a delivery path for a
// session token. On load, any other hash (#access_token=... or anything
// else) is replaced with #/overview: no token is read from the fragment,
// and the session already held in sessionStorage is left untouched.
//
// After either path, GET /api/auth/me decides who is signed in. The
// sidebar shows the user's username, or sub when there is none. Sign out
// clears the token and returns to sign-in.
(function (global) {
  'use strict';

  var CONFIG_PATH = '/api/auth/config';
  var ME_PATH = '/api/auth/me';
  var STATE_KEY = 'aierp_oauth_state';
  var VERIFIER_KEY = 'aierp_pkce_verifier';

  var config = null;
  var user = null;
  var bootstrapped = false;
  var redirecting = false;

  function $(id) {
    return document.getElementById(id);
  }

  /* ---------------------------- PKCE helpers ------------------------- */

  function base64url(bytes) {
    var binary = '';
    for (var i = 0; i < bytes.length; i++) {
      binary += String.fromCharCode(bytes[i]);
    }
    return global.btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  }

  function randomToken(byteLength) {
    var bytes = new Uint8Array(byteLength);
    global.crypto.getRandomValues(bytes);
    return base64url(bytes);
  }

  // A self-contained SHA-256 for contexts without crypto.subtle (e.g.
  // plain http, where the Web Crypto subtle API is not exposed): takes a
  // byte sequence and returns the 32-byte digest, byte-identical to
  // crypto.subtle.digest('SHA-256', ...).
  function sha256Bytes(input) {
    var K = [
      0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1,
      0x923f82a4, 0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3,
      0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786,
      0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
      0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147,
      0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
      0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b,
      0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
      0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a,
      0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
      0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2
    ];

    var length = input.length;
    var paddedLength = 64 * (Math.floor((length + 8) / 64) + 1);
    var padded = new Uint8Array(paddedLength);
    padded.set(input);
    padded[length] = 0x80;

    // The message length in bits, as 64-bit big-endian.
    var bitLength = length * 8;
    var high = Math.floor(bitLength / 4294967296);
    var low = (bitLength - high * 4294967296) | 0;
    padded[paddedLength - 8] = (high >>> 24) & 0xff;
    padded[paddedLength - 7] = (high >>> 16) & 0xff;
    padded[paddedLength - 6] = (high >>> 8) & 0xff;
    padded[paddedLength - 5] = high & 0xff;
    padded[paddedLength - 4] = (low >>> 24) & 0xff;
    padded[paddedLength - 3] = (low >>> 16) & 0xff;
    padded[paddedLength - 2] = (low >>> 8) & 0xff;
    padded[paddedLength - 1] = low & 0xff;

    var h0 = 0x6a09e667, h1 = 0xbb67ae85, h2 = 0x3c6ef372, h3 = 0xa54ff53a;
    var h4 = 0x510e527f, h5 = 0x9b05688c, h6 = 0x1f83d9ab, h7 = 0x5be0cd19;
    var w = new Array(64);

    for (var offset = 0; offset < paddedLength; offset += 64) {
      for (var i = 0; i < 16; i++) {
        var j = offset + i * 4;
        w[i] =
          (padded[j] << 24) |
          (padded[j + 1] << 16) |
          (padded[j + 2] << 8) |
          padded[j + 3];
      }
      for (i = 16; i < 64; i++) {
        var s0 =
          ((w[i - 15] >>> 7) | (w[i - 15] << 25)) ^
          ((w[i - 15] >>> 18) | (w[i - 15] << 14)) ^
          (w[i - 15] >>> 3);
        var s1 =
          ((w[i - 2] >>> 17) | (w[i - 2] << 15)) ^
          ((w[i - 2] >>> 19) | (w[i - 2] << 13)) ^
          (w[i - 2] >>> 10);
        w[i] = (w[i - 16] + s0 + w[i - 7] + s1) | 0;
      }

      var a = h0, b = h1, c = h2, d = h3, e = h4, f = h5, g = h6, h = h7;
      for (i = 0; i < 64; i++) {
        var bigS1 =
          ((e >>> 6) | (e << 26)) ^
          ((e >>> 11) | (e << 21)) ^
          ((e >>> 25) | (e << 7));
        var ch = (e & f) ^ (~e & g);
        var t1 = (h + bigS1 + ch + K[i] + w[i]) | 0;
        var bigS0 =
          ((a >>> 2) | (a << 30)) ^
          ((a >>> 13) | (a << 19)) ^
          ((a >>> 22) | (a << 10));
        var maj = (a & b) ^ (a & c) ^ (b & c);
        var t2 = (bigS0 + maj) | 0;
        h = g; g = f; f = e; e = (d + t1) | 0;
        d = c; c = b; b = a; a = (t1 + t2) | 0;
      }

      h0 = (h0 + a) | 0; h1 = (h1 + b) | 0;
      h2 = (h2 + c) | 0; h3 = (h3 + d) | 0;
      h4 = (h4 + e) | 0; h5 = (h5 + f) | 0;
      h6 = (h6 + g) | 0; h7 = (h7 + h) | 0;
    }

    var state = [h0, h1, h2, h3, h4, h5, h6, h7];
    var digest = new Uint8Array(32);
    for (var n = 0; n < 8; n++) {
      var word = state[n] >>> 0;
      digest[n * 4] = (word >>> 24) & 0xff;
      digest[n * 4 + 1] = (word >>> 16) & 0xff;
      digest[n * 4 + 2] = (word >>> 8) & 0xff;
      digest[n * 4 + 3] = word & 0xff;
    }
    return digest;
  }

  // The PKCE code challenge, RFC 7636: base64url(SHA-256(verifier))
  // without padding. crypto.subtle is used when it exists (secure
  // contexts); otherwise the self-contained fallback computes the
  // identical digest, so production sign-in works from plain http.
  async function pkceChallenge(verifier) {
    var data = new TextEncoder().encode(verifier);
    var digestBytes;
    if (global.crypto && global.crypto.subtle) {
      var digest = await global.crypto.subtle.digest('SHA-256', data);
      digestBytes = new Uint8Array(digest);
    } else {
      digestBytes = sha256Bytes(data);
    }
    return base64url(digestBytes);
  }

  /* ---------------------------- UI helpers --------------------------- */

  function showAuthError(message) {
    var error = $('auth-error');
    if (error) {
      error.textContent = message;
      error.hidden = false;
    }
  }

  function hideAuthError() {
    var error = $('auth-error');
    if (error) {
      error.textContent = '';
      error.hidden = true;
    }
  }

  function showSignInScreen() {
    $('auth').hidden = false;
    $('app').hidden = true;
    var production = !!(config && config.authMode === 'production');
    $('signin-form').hidden = production;
    $('retry-button').hidden = !production;
    $('auth-sub').textContent = production
      ? 'Sign in with your identity provider to continue.'
      : 'Sign in to open the planner screen.';
    var input = $('token-input');
    if (input) {
      input.value = '';
    }
  }

  function applyRoleGating() {
    // The shell is read-only for non-admins; anything marked
    // [data-admin-only] is shown only to users with the admin role.
    var roles = user && Array.isArray(user.roles) ? user.roles : [];
    var isAdmin = roles.indexOf('admin') !== -1;
    var controls = document.querySelectorAll('[data-admin-only]');
    for (var i = 0; i < controls.length; i++) {
      controls[i].hidden = !isAdmin;
    }
  }

  function showAppScreen() {
    $('auth').hidden = true;
    $('app').hidden = false;
    $('user-name').textContent = (user && (user.username || user.sub)) || '';
    applyRoleGating();
  }

  /* ------------------------- production flow ------------------------- */

  async function beginPkceRedirect() {
    if (redirecting) {
      return;
    }
    redirecting = true;
    try {
      var verifier = randomToken(32);
      var state = randomToken(16);
      global.sessionStorage.setItem(VERIFIER_KEY, verifier);
      global.sessionStorage.setItem(STATE_KEY, state);
      var endpoint = new URL(config.issuer + '/protocol/openid-connect/auth');
      endpoint.searchParams.set('client_id', config.clientId);
      endpoint.searchParams.set('response_type', 'code');
      endpoint.searchParams.set('scope', 'openid');
      endpoint.searchParams.set('redirect_uri', global.location.origin + '/');
      endpoint.searchParams.set('state', state);
      endpoint.searchParams.set('code_challenge', await pkceChallenge(verifier));
      endpoint.searchParams.set('code_challenge_method', 'S256');
      global.location.assign(endpoint.toString());
    } catch (error) {
      redirecting = false;
      showAuthError('Could not start sign-in. Please try again.');
      showSignInScreen();
    }
  }

  async function exchangeCode(code, state) {
    var expected = null;
    var verifier = null;
    try {
      expected = global.sessionStorage.getItem(STATE_KEY);
      verifier = global.sessionStorage.getItem(VERIFIER_KEY);
      global.sessionStorage.removeItem(STATE_KEY);
      global.sessionStorage.removeItem(VERIFIER_KEY);
    } catch (error) {
      // storage unavailable
    }

    if (!code || !expected || state !== expected || !verifier) {
      showAuthError(
        'Sign-in failed: the security check did not match. Please try again.'
      );
      return false;
    }

    var body = new URLSearchParams();
    body.set('grant_type', 'authorization_code');
    body.set('code', code);
    body.set('redirect_uri', global.location.origin + '/');
    body.set('client_id', config.clientId);
    body.set('code_verifier', verifier);

    var response;
    try {
      response = await global.fetch(config.issuer + '/protocol/openid-connect/token', {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: body.toString(),
      });
    } catch (networkError) {
      showAuthError('The identity provider could not be reached. Please try again.');
      return false;
    }

    var payload = null;
    try {
      payload = await response.json();
    } catch (parseError) {
      // fall through
    }
    if (!response.ok || !payload || !payload.access_token) {
      showAuthError('Sign-in failed: the identity provider refused the code. Please try again.');
      return false;
    }

    Api.setToken(payload.access_token);
    // Remove the one-time code from the address bar.
    global.history.replaceState(null, '', global.location.pathname + global.location.hash);
    return true;
  }

  /* --------------------------- signed-in check ----------------------- */

  async function fetchMe() {
    try {
      var me = await Api.get(ME_PATH);
    } catch (error) {
      // A 401 already cleared the token and restarted sign-in in api.js;
      // any other failure leaves the session in its current state.
      return false;
    }
    if (me && me.authenticated && me.user) {
      user = me.user;
      return true;
    }
    Api.setToken(null);
    return false;
  }

  /* ------------------------------ flow ------------------------------- */

  function sanitizeHashRoute() {
    // The URL fragment is a view route, never a token: no fragment
    // (#access_token=... or anything else) may establish or change a
    // session. A hash that is not one of the view routes — #/overview,
    // #/queue, #/log, each with an optional query string — is replaced
    // with #/overview. Nothing is read from the fragment as a token, and
    // the sessionStorage session is neither cleared nor modified.
    var base = (global.location.hash || '').split('?')[0];
    if (/^#\/(overview|queue|log)$/.test(base)) {
      return;
    }
    global.history.replaceState(
      null,
      '',
      global.location.pathname + global.location.search + '#/overview'
    );
  }

  async function bootstrap() {
    if (bootstrapped) {
      return;
    }
    bootstrapped = true;

    sanitizeHashRoute();

    try {
      config = await Api.get(CONFIG_PATH);
    } catch (error) {
      showAuthError('Could not load the sign-in configuration. Please reload the page.');
      showSignInScreen();
      return;
    }
    if (!config || typeof config.authMode !== 'string') {
      config = { authMode: 'development' };
    }

    var params = new URLSearchParams(global.location.search);
    if (params.has('code')) {
      var exchanged = await exchangeCode(params.get('code'), params.get('state') || '');
      if (!exchanged) {
        showSignInScreen();
        return;
      }
    }

    if (Api.getToken() && (await fetchMe())) {
      showAppScreen();
      return;
    }

    if (config.authMode === 'production') {
      beginPkceRedirect();
      return;
    }
    showSignInScreen();
  }

  async function signIn(token) {
    if (!token) {
      showAuthError('Enter a token to sign in.');
      return;
    }
    Api.setToken(token);
    if (await fetchMe()) {
      hideAuthError();
      showAppScreen();
      return;
    }
    // Sign-in failed: a 401 (api.js already cleared the token and
    // restarted the sign-in flow) or any other rejection — the card
    // shows the rejection and the sign-in screen either way.
    showAuthError('The token was rejected. Please try again.');
    showSignInScreen();
  }

  function signOut() {
    user = null;
    Api.setToken(null);
    startSignIn();
  }

  function startSignIn(reason) {
    if (config && config.authMode === 'production') {
      beginPkceRedirect();
      return;
    }
    if (reason === 'unauthorized') {
      showAuthError('The token was rejected. Please try again.');
    } else {
      hideAuthError();
    }
    showSignInScreen();
  }

  function retrySignIn() {
    hideAuthError();
    if (config && config.authMode === 'production') {
      beginPkceRedirect();
      return;
    }
    showSignInScreen();
  }

  global.Auth = {
    bootstrap: bootstrap,
    signIn: signIn,
    signOut: signOut,
    startSignIn: startSignIn,
    retrySignIn: retrySignIn,
    pkceChallenge: pkceChallenge,
  };
})(window);
