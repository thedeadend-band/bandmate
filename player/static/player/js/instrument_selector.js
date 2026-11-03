(function() {
  if (window.instrumentSelection) return;

  const data = document.getElementById('selected-instruments-data');
  const selected = new Set(data ? JSON.parse(data.textContent) : ['master']);
  window.instrumentSelection = selected;

  function csrfToken() {
    return document.cookie.match(/(?:^|; )csrftoken=([^;]+)/)?.[1] || '';
  }

  function render() {
    document.querySelectorAll('[data-instrument]').forEach(button => {
      const active = selected.has(button.dataset.instrument);
      button.classList.toggle('active', active);
      button.setAttribute('aria-pressed', active ? 'true' : 'false');
    });
  }

  async function save() {
    try {
      const response = await fetch('/api/playback-preferences/', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': csrfToken(),
        },
        body: JSON.stringify({ enabled_categories: Array.from(selected) }),
      });
      if (!response.ok) throw new Error(`Preference save failed (${response.status})`);
    } catch (error) {
      console.error(error);
    }
  }

  document.querySelectorAll('[data-instrument]').forEach(button => {
    button.addEventListener('click', () => {
      const category = button.dataset.instrument;
      if (category === 'master') {
        selected.clear();
        selected.add('master');
      } else {
        selected.delete('master');
        if (selected.has(category)) selected.delete(category);
        else selected.add(category);
        if (!selected.size) selected.add('master');
      }
      render();
      save();
      window.dispatchEvent(new CustomEvent('instrument-selection-change', {
        detail: { enabledCategories: Array.from(selected) },
      }));
    });
  });

  render();
})();
