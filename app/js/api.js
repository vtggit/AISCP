// AIERP planner screen — the single HTTP layer.
//
// Every /api/* call in the app goes through Api.get / Api.request:
// the signed-in user's bearer token is attached automatically, and a
// 401 means "signed out" — the token is cleared and the sign-in flow
// restarts (the handler is installed by the shell, see auth.js/app.js).
(function (global) {
  'use strict';

  var TOKEN_KEY = 'aierp_token';

  function getToken() {
    try {
      return global.sessionStorage.getItem(TOKEN_KEY);
    } catch (error) {
      return null;
    }
  }

  function setToken(token) {
    try {
      if (token) {
        global.sessionStorage.setItem(TOKEN_KEY, token);
      } else {
        global.sessionStorage.removeItem(TOKEN_KEY);
      }
    } catch (error) {
      // Storage may be unavailable (e.g. blocked cookies): the app keeps
      // working, it just cannot remember the session between reloads.
    }
  }

  var onUnauthorized = null;

  function setUnauthorizedHandler(handler) {
    onUnauthorized = typeof handler === 'function' ? handler : null;
  }

  function errorMessage(payload, status) {
    if (payload && payload.detail) {
      if (typeof payload.detail === 'string') {
        return payload.detail;
      }
      try {
        return JSON.stringify(payload.detail);
      } catch (error) {
        // fall through to the generic message
      }
    }
    return 'Request failed (' + status + ').';
  }

  async function request(path, options) {
    options = options || {};
    var headers = {};
    var source = options.headers || {};
    for (var key in source) {
      if (Object.prototype.hasOwnProperty.call(source, key)) {
        headers[key] = source[key];
      }
    }
    var token = getToken();
    if (token) {
      headers['Authorization'] = 'Bearer ' + token;
    }

    var response;
    try {
      response = await global.fetch(path, {
        method: options.method || 'GET',
        headers: headers,
        body: options.body,
      });
    } catch (networkError) {
      var offline = new Error('The server could not be reached. Please try again.');
      offline.network = true;
      throw offline;
    }

    if (response.status === 401) {
      setToken(null);
      if (onUnauthorized) {
        onUnauthorized('unauthorized');
      }
      var signedOut = new Error('Signed out.');
      signedOut.unauthorized = true;
      throw signedOut;
    }

    var payload = null;
    try {
      payload = await response.json();
    } catch (parseError) {
      // A non-JSON body: fine on success (empty body), informative below.
    }

    if (!response.ok) {
      var apiError = new Error(errorMessage(payload, response.status));
      apiError.status = response.status;
      throw apiError;
    }
    return payload;
  }

  function get(path) {
    return request(path);
  }

  // Download a file the API serves (e.g. a CSV export): the same bearer
  // token as request, and a 401 means the same "signed out" — the token
  // is cleared, the unauthorized handler runs, and the promise rejects
  // with `unauthorized` set. Any other non-ok response rejects with an
  // Error whose `status` is the response status. On success the body is
  // read as a Blob and saved through a temporary <a download> click,
  // which is removed and whose object URL is revoked afterwards.
  async function download(path, filename) {
    var headers = {};
    var token = getToken();
    if (token) {
      headers['Authorization'] = 'Bearer ' + token;
    }

    var response;
    try {
      response = await global.fetch(path, {
        method: 'GET',
        headers: headers,
      });
    } catch (networkError) {
      var offline = new Error('The server could not be reached. Please try again.');
      offline.network = true;
      throw offline;
    }

    if (response.status === 401) {
      setToken(null);
      if (onUnauthorized) {
        onUnauthorized('unauthorized');
      }
      var signedOut = new Error('Signed out.');
      signedOut.unauthorized = true;
      throw signedOut;
    }

    if (!response.ok) {
      var payload = null;
      try {
        payload = await response.json();
      } catch (parseError) {
        // A non-JSON body: the status below is what matters.
      }
      var apiError = new Error(errorMessage(payload, response.status));
      apiError.status = response.status;
      throw apiError;
    }

    var blob = await response.blob();
    var url = URL.createObjectURL(blob);
    var anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = String(filename);
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
  }

  global.Api = {
    TOKEN_KEY: TOKEN_KEY,
    getToken: getToken,
    setToken: setToken,
    setUnauthorizedHandler: setUnauthorizedHandler,
    get: get,
    request: request,
    download: download,
  };
})(window);
