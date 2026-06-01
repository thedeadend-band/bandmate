class LyricsPlayer {
  constructor(items, mode) {
    this.items = items || [];
    this.mode = mode || 'setlist';
    this.currentIndex = 0;
    this.audioContext = new (window.AudioContext || window.webkitAudioContext)();
    this.source = null;
    this.gainNode = this.audioContext.createGain();
    this.gainNode.connect(this.audioContext.destination);
    this.buffer = null;
    this.isPlaying = false;
    this.playStartTime = 0;
    this.playOffset = 0;
    this.duration = 0;
    this.animationId = null;
    this.lyrics = [];
    this.lyricOffset = 0;
    this.currentLyricIndex = -1;
    this.isScrubbingLyrics = false;
    this._lyricsScrollEndTimer = null;
    this._bindEvents();
    if (this.items.length) {
      this.loadIndex(0);
    }
  }

  _bindEvents() {
    document.getElementById('ly-play')?.addEventListener('click', () => {
      if (this.isPlaying) this.pause();
      else this.play();
    });
    document.getElementById('ly-prev')?.addEventListener('click', () => {
      if (this.mode === 'single') {
        this.seekTo(0);
        return;
      }
      if (this.currentTime() > 3) {
        this.seekTo(0);
      } else {
        this.loadIndex(Math.max(0, this.currentIndex - 1), this.isPlaying);
      }
    });
    document.getElementById('ly-next')?.addEventListener('click', () => {
      if (this.mode === 'single') {
        this.stop();
        return;
      }
      this.loadIndex(Math.min(this.items.length - 1, this.currentIndex + 1), this.isPlaying);
    });
    document.addEventListener('keydown', (e) => {
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
      if (e.code === 'Space') {
        e.preventDefault();
        if (this.isPlaying) this.pause();
        else this.play();
      }
    });

    const progress = document.getElementById('ly-progress');
    if (progress) {
      progress.addEventListener('input', () => {
        const pct = Math.max(0, Math.min(1000, Number(progress.value || 0))) / 1000;
        this.seekTo((this.duration || 0) * pct);
      });
    }

    this._bindLyricsSeeking();
  }

  async loadIndex(index, autoPlay = false) {
    if (index < 0 || index >= this.items.length) return;
    const wasPlaying = this.isPlaying;
    this.stop();
    this.currentIndex = index;
    this.currentLyricIndex = -1;
    this.playOffset = 0;
    const item = this.items[index];
    document.getElementById('ly-current-title').textContent = item.display || item.song_name;
    document.getElementById('ly-current-detail').textContent = item.detail || '';
    if (this.mode === 'setlist') {
      const next = this.items[index + 1];
      document.getElementById('ly-next-title').textContent = next ? (next.display || next.song_name) : '—';
    }

    try {
      const infoResp = await fetch(`/api/songs/${encodeURIComponent(item.song_name)}/info/`);
      const infoData = infoResp.ok ? await infoResp.json() : null;
      this.lyrics = infoData?.lyrics || [];
      this.lyricOffset = infoData?.info?.lyric_offset_secs || 0;
      this._renderLyrics();

      const audioResp = await fetch(`/api/songs/${encodeURIComponent(item.song_name)}/master/audio/`);
      if (!audioResp.ok) throw new Error('Audio failed');
      const arr = await audioResp.arrayBuffer();
      this.buffer = await this.audioContext.decodeAudioData(arr);
      this.duration = this.buffer.duration || 0;
      this._updateTime();

      if (autoPlay || wasPlaying) await this.play();
    } catch (e) {
      console.error('Lyrics player load failed', e);
      document.getElementById('ly-lyrics-scroller').innerHTML =
        '<div class="lyrics-line active">Failed to load song.</div>';
    }
  }

  _renderLyrics() {
    const scroller = document.getElementById('ly-lyrics-scroller');
    if (!this.lyrics.length) {
      scroller.innerHTML = '<div class="lyrics-line active">No synced lyrics found.</div>';
      return;
    }
    scroller.style.transform = '';
    scroller.innerHTML = this.lyrics.map(l =>
      `<div class="lyrics-line" data-time="${l.time}">${l.text || '&nbsp;'}</div>`
    ).join('');
  }

  async play() {
    if (!this.buffer || this.isPlaying) return;
    await this.audioContext.resume();
    this.isPlaying = true;
    const startAt = this.audioContext.currentTime + 0.03;
    this.playStartTime = startAt;
    const src = this.audioContext.createBufferSource();
    src.buffer = this.buffer;
    src.connect(this.gainNode);
    src.start(startAt, this.playOffset);
    src.onended = () => {
      if (!this.isPlaying) return;
      if (this.mode === 'setlist' && this.currentIndex < this.items.length - 1) {
        this.loadIndex(this.currentIndex + 1, true);
      } else {
        this.stop();
      }
    };
    this.source = src;
    this._showPause(true);
    this._startAnimation();
  }

  pause() {
    if (!this.isPlaying) return;
    this.playOffset = this.currentTime();
    this.isPlaying = false;
    if (this.source) {
      try { this.source.stop(); } catch (_) {}
      this.source = null;
    }
    this._showPause(false);
    this._stopAnimation();
  }

  stop() {
    this.pause();
    this.playOffset = 0;
    this._updateTime();
    this._updateLyrics();
  }

  async seekTo(seconds) {
    const wasPlaying = this.isPlaying;
    this.pause();
    this.playOffset = Math.max(0, Math.min(seconds, this.duration || 0));
    this._updateTime();
    this.currentLyricIndex = -1;
    this._updateLyrics();
    if (wasPlaying) await this.play();
  }

  currentTime() {
    if (!this.isPlaying) return this.playOffset;
    const elapsed = this.audioContext.currentTime - this.playStartTime;
    return Math.min(this.playOffset + elapsed, this.duration || 0);
  }

  _startAnimation() {
    const tick = () => {
      if (!this.isPlaying) return;
      this._updateTime();
      this._updateLyrics();
      this.animationId = requestAnimationFrame(tick);
    };
    tick();
  }

  _stopAnimation() {
    if (this.animationId) {
      cancelAnimationFrame(this.animationId);
      this.animationId = null;
    }
  }

  _showPause(show) {
    const btn = document.getElementById('ly-play');
    if (!btn) return;
    btn.querySelector('.icon-play').style.display = show ? 'none' : '';
    btn.querySelector('.icon-pause').style.display = show ? '' : 'none';
    btn.classList.toggle('active', show);
  }

  _updateTime() {
    const el = document.getElementById('ly-time');
    if (!el) return;
    el.textContent = `${this._fmt(this.currentTime())} / ${this._fmt(this.duration)}`;
    const progress = document.getElementById('ly-progress');
    if (progress && this.duration > 0) {
      progress.value = String(Math.round((this.currentTime() / this.duration) * 1000));
    }
  }

  _fmt(s) {
    if (!s || isNaN(s)) return '0:00';
    const m = Math.floor(s / 60);
    const sec = Math.floor(s % 60);
    return `${m}:${sec.toString().padStart(2, '0')}`;
  }

  _updateLyrics() {
    if (!this.lyrics.length) return;
    if (this.isScrubbingLyrics) return;
    const ct = this.currentTime();
    let idx = -1;
    for (let i = 0; i < this.lyrics.length; i++) {
      if (ct >= this.lyrics[i].time + this.lyricOffset) idx = i;
      else break;
    }
    if (idx === this.currentLyricIndex) return;
    this.currentLyricIndex = idx;
    const lines = document.querySelectorAll('#ly-lyrics-scroller .lyrics-line');
    if (!lines.length) return;
    lines.forEach((el, i) => {
      el.classList.toggle('active', i === idx);
      el.classList.toggle('near', i >= idx - 2 && i <= idx + 2 && i !== idx);
      el.classList.toggle('past', i < idx - 2);
      el.classList.toggle('future', i > idx + 2);
    });
    const scroller = document.getElementById('ly-lyrics-scroller');
    if (idx >= 0 && lines[idx]) {
      const container = document.getElementById('ly-main');
      const lineEl = lines[idx];
      const containerH = container.clientHeight;
      const targetY = lineEl.offsetTop - containerH / 2 + lineEl.offsetHeight / 2;
      scroller.style.transform = `translateY(${-targetY}px)`;
    }
  }

  _bindLyricsSeeking() {
    const main = document.getElementById('ly-main');
    const scroller = document.getElementById('ly-lyrics-scroller');
    if (!main || !scroller) return;

    const getCurrentTranslateY = () => {
      const m = (scroller.style.transform || '').match(/translateY\((.+?)px\)/);
      return m ? parseFloat(m[1]) : 0;
    };

    const findCenteredLineIndex = () => {
      const lines = scroller.querySelectorAll('.lyrics-line[data-time]');
      if (!lines.length) return -1;
      const centerY = main.getBoundingClientRect().top + main.clientHeight / 2;
      let closest = -1;
      let distBest = Infinity;
      lines.forEach((el, i) => {
        const r = el.getBoundingClientRect();
        const mid = r.top + r.height / 2;
        const d = Math.abs(mid - centerY);
        if (d < distBest) {
          distBest = d;
          closest = i;
        }
      });
      return closest;
    };

    const seekToCenteredLine = () => {
      const idx = findCenteredLineIndex();
      if (idx < 0 || !this.lyrics[idx]) return;
      const t = (this.lyrics[idx].time || 0) + (this.lyricOffset || 0);
      this.isScrubbingLyrics = false;
      this.seekTo(Math.max(0, t));
    };

    const endScrubSoon = () => {
      clearTimeout(this._lyricsScrollEndTimer);
      this._lyricsScrollEndTimer = setTimeout(seekToCenteredLine, 180);
    };

    scroller.addEventListener('click', (e) => {
      const line = e.target.closest('.lyrics-line[data-time]');
      if (!line) return;
      const t = Number(line.dataset.time || 0) + (this.lyricOffset || 0);
      this.isScrubbingLyrics = false;
      this.seekTo(Math.max(0, t));
    });

    main.addEventListener('wheel', (e) => {
      if (!this.lyrics.length) return;
      e.preventDefault();
      this.isScrubbingLyrics = true;
      const current = getCurrentTranslateY();
      const next = current - e.deltaY;
      scroller.style.transform = `translateY(${next}px)`;
      endScrubSoon();
    }, { passive: false });

    let dragging = false;
    let startY = 0;
    let startTranslate = 0;
    main.addEventListener('pointerdown', (e) => {
      if (!this.lyrics.length) return;
      dragging = true;
      this.isScrubbingLyrics = true;
      startY = e.clientY;
      startTranslate = getCurrentTranslateY();
      scroller.style.transition = 'none';
      main.setPointerCapture?.(e.pointerId);
    });
    main.addEventListener('pointermove', (e) => {
      if (!dragging) return;
      const delta = e.clientY - startY;
      scroller.style.transform = `translateY(${startTranslate + delta}px)`;
    });
    const endDrag = () => {
      if (!dragging) return;
      dragging = false;
      scroller.style.transition = '';
      endScrubSoon();
    };
    main.addEventListener('pointerup', endDrag);
    main.addEventListener('pointercancel', endDrag);
  }
}

document.addEventListener('DOMContentLoaded', () => {
  const mode = window.LYRICS_PLAYER_MODE || 'setlist';
  let items = [];
  if (mode === 'single') {
    const el = document.getElementById('ly-single-item');
    if (el) items = [JSON.parse(el.textContent)];
  } else {
    const el = document.getElementById('ly-items-data');
    if (el) items = JSON.parse(el.textContent);
  }
  if (!items.length) return;
  window.lyricsPlayer = new LyricsPlayer(items, mode);
});
