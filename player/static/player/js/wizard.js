/**
 * New Song Wizard – shared JS for all wizard steps.
 *
 * Provides:
 *  - pollTaskStatus(wizardId, csrfToken, onUpdate) for background task polling
 *  - Utility helpers as more phases are added
 */

const Wizard = (function () {
  'use strict';

  function getCSRFToken() {
    const el = document.querySelector('[name=csrfmiddlewaretoken]');
    if (el) return el.value;
    const match = document.cookie.match(/csrftoken=([^;]+)/);
    return match ? match[1] : '';
  }

  /**
   * Poll a wizard's background task status until it finishes.
   *
   * @param {number} wizardId
   * @param {function} onUpdate - called with {status, progress, message}
   * @param {number} intervalMs - polling interval (default 1500)
   * @returns {function} stop - call to cancel polling
   */
  function pollTaskStatus(wizardId, onUpdate, intervalMs = 1500) {
    let timer = null;
    let stopped = false;

    async function poll() {
      if (stopped) return;
      try {
        const resp = await fetch(`/api/songs/new/${wizardId}/task-status/`, {
          headers: { 'X-CSRFToken': getCSRFToken() },
        });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();
        onUpdate(data);

        if (data.status === 'done' || data.status === 'error') {
          stopped = true;
          return;
        }
      } catch (err) {
        console.error('Wizard poll error:', err);
      }
      if (!stopped) {
        timer = setTimeout(poll, intervalMs);
      }
    }

    poll();

    return function stop() {
      stopped = true;
      if (timer) clearTimeout(timer);
    };
  }

  /**
   * Generic POST helper (JSON body, JSON response).
   */
  async function postJSON(url, body = {}) {
    const resp = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': getCSRFToken(),
      },
      body: JSON.stringify(body),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return resp.json();
  }

  return { getCSRFToken, pollTaskStatus, postJSON };
})();
