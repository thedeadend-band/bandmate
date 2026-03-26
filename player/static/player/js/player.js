/* ========================================================================
   BandMate – Synchronized multi-track audio player
   Uses the Web Audio API to decode and play all tracks in lock-step.
   ======================================================================== */

class MultiTrackPlayer {
  constructor(songName, trackData) {
    this.songName = songName;
    this.audioContext = null;
    this.tracks = [];
    this.isPlaying = false;
    this.playStartTime = 0;
    this.playOffset = 0;
    this.duration = 0;
    this.animationId = null;
    this.loadedCount = 0;

    this.lyrics = null;
    this.lyricOffset = 0;
    this.currentLyricIndex = -1;

    this._wakeLock = null;

    this._initAudioContext();
    this._initTracks(trackData);
    this._initLyrics();
    this._bindEvents();

    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible' && this.isPlaying) {
        this._requestWakeLock();
      }
    });
  }

  async _requestWakeLock() {
    if (!('wakeLock' in navigator)) return;
    try { this._wakeLock = await navigator.wakeLock.request('screen'); }
    catch (_) {}
  }

  _releaseWakeLock() {
    if (this._wakeLock) {
      this._wakeLock.release().catch(function() {});
      this._wakeLock = null;
    }
  }

  /* ---- Initialisation ------------------------------------------------- */

  _initAudioContext() {
    this.audioContext = new (window.AudioContext || window.webkitAudioContext)();

    // Route audio through a MediaStreamDestination → <audio> element so that
    // iOS treats playback as "media" rather than "ambient", bypassing the
    // hardware mute/silent switch.
    try {
      this._streamDest = this.audioContext.createMediaStreamDestination();
      this._iosAudio = document.createElement('audio');
      this._iosAudio.setAttribute('playsinline', '');
      this._iosAudio.srcObject = this._streamDest.stream;
      this.outputNode = this._streamDest;
    } catch (e) {
      this.outputNode = this.audioContext.destination;
    }
  }

  _initTracks(trackData) {
    trackData.forEach((data, index) => {
      const el = document.querySelector(`.track[data-index="${index}"]`);
      const canvas = el.querySelector('.waveform-canvas');
      const track = {
        name: data.name,
        filename: data.filename,
        buffer: null,
        source: null,
        gainNode: this.audioContext.createGain(),
        panNode: this.audioContext.createStereoPanner(),
        volume: 1,
        pan: 0,
        muted: false,
        soloed: false,
        peaks: null,
        duration: 0,
        loaded: false,
        canvas,
        element: el,
        dpr: 1,
        displayWidth: 0,
        displayHeight: 0,
      };
      track.gainNode.connect(track.panNode);
      track.panNode.connect(this.outputNode);
      this.tracks.push(track);
      this._loadTrack(track);
    });
  }

  /* ---- Loading -------------------------------------------------------- */

  async _loadTrack(track) {
    const overlay = track.element.querySelector('.loading-overlay');
    const label = overlay.querySelector('span');

    try {
      label.textContent = 'Loading waveform\u2026';
      const wfUrl =
        `/api/songs/${encodeURIComponent(this.songName)}/tracks/` +
        `${encodeURIComponent(track.filename)}/waveform/`;
      const wfResp = await fetch(wfUrl);
      if (!wfResp.ok) throw new Error(`Waveform ${wfResp.status}`);
      const wfData = await wfResp.json();
      track.channels = wfData.channels || 1;
      if (track.channels >= 2) {
        track.peaksLeft = wfData.peaks_left;
        track.peaksRight = wfData.peaks_right;
        track.peaks = wfData.peaks_left; // used for length reference
      } else {
        track.peaks = wfData.peaks;
      }
      track.duration = wfData.duration;

      this._setupCanvas(track);
      this._drawWaveform(track);

      if (track.duration > this.duration) {
        this.duration = track.duration;
        this._updateTimeDisplay();
      }

      label.textContent = 'Loading audio\u2026';
      const audioUrl =
        `/api/songs/${encodeURIComponent(this.songName)}/tracks/` +
        `${encodeURIComponent(track.filename)}/audio/`;
      const audioResp = await fetch(audioUrl);
      if (!audioResp.ok) throw new Error(`Audio ${audioResp.status}`);
      const arrayBuf = await audioResp.arrayBuffer();
      track.buffer = await this.audioContext.decodeAudioData(arrayBuf);

      track.loaded = true;
      overlay.classList.add('hidden');
      this.loadedCount++;
      this._updateLoadingStatus();
    } catch (err) {
      console.error(`Failed to load track "${track.name}":`, err);
      label.textContent = 'Load failed';
      label.style.color = getComputedStyle(document.documentElement).getPropertyValue('--accent-red');
    }
  }

  _updateLoadingStatus() {
    const el = document.getElementById('loading-status');
    if (this.loadedCount < this.tracks.length) {
      el.textContent = `${this.loadedCount}/${this.tracks.length} tracks loaded`;
    } else {
      el.textContent = '';
    }
    if (this.loadedCount > 0) {
      document.getElementById('play-btn').classList.remove('disabled');
    }
  }

  /* ---- Lyrics --------------------------------------------------------- */

  _initLyrics() {
    const lyricsEl = document.getElementById('lyrics-data');
    const infoEl = document.getElementById('song-info-data');
    if (!lyricsEl) return;
    this.lyrics = JSON.parse(lyricsEl.textContent);
    if (infoEl) {
      const info = JSON.parse(infoEl.textContent);
      this.lyricOffset = info.lyric_offset_secs || 0;
    }
  }

  _updateLyrics() {
    if (!this.lyrics || !this.lyrics.length) return;
    const ct = this.currentTime();
    let idx = -1;
    for (let i = 0; i < this.lyrics.length; i++) {
      if (ct >= this.lyrics[i].time + this.lyricOffset) idx = i;
      else break;
    }
    if (idx === this.currentLyricIndex) return;
    this.currentLyricIndex = idx;

    const lines = document.querySelectorAll('#lyrics-scroller .lyrics-line');
    if (!lines.length) return;
    lines.forEach((el, i) => {
      el.classList.toggle('active', i === idx);
      el.classList.toggle('near', i >= idx - 2 && i <= idx + 2 && i !== idx);
      el.classList.toggle('past', i < idx - 2);
      el.classList.toggle('future', i > idx + 2);
    });

    const scroller = document.getElementById('lyrics-scroller');
    if (idx >= 0 && lines[idx]) {
      const container = document.getElementById('lyrics-container');
      const lineEl = lines[idx];
      const containerH = container.clientHeight;
      const targetY = lineEl.offsetTop - containerH / 2 + lineEl.offsetHeight / 2;
      scroller.style.transform = `translateY(${-targetY}px)`;
    } else if (scroller) {
      scroller.style.transform = '';
    }
  }

  /* ---- Canvas / Waveform ---------------------------------------------- */

  _setupCanvas(track) {
    const container = track.canvas.parentElement;
    const rect = container.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    track.canvas.width = rect.width * dpr;
    track.canvas.height = rect.height * dpr;
    track.dpr = dpr;
    track.displayWidth = rect.width;
    track.displayHeight = rect.height;
  }

  _drawWaveform(track) {
    if (!track.peaks || !track.peaks.length) return;
    const ctx = track.canvas.getContext('2d');
    const dpr = track.dpr;
    const w = track.displayWidth;
    const h = track.displayHeight;
    const cs = getComputedStyle(document.documentElement);
    const wfA = cs.getPropertyValue('--wf-color-a').trim() || '#4a9eff';
    const wfB = cs.getPropertyValue('--wf-color-b').trim() || '#7c5cfc';
    const centerLine = cs.getPropertyValue('--wf-center-line').trim() || 'rgba(255,255,255,0.07)';
    const dividerColor = cs.getPropertyValue('--wf-divider').trim() || centerLine;

    ctx.save();
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, w, h);

    if (track.channels >= 2 && track.peaksLeft && track.peaksRight) {
      const mid = h / 2;
      const barW = w / track.peaksLeft.length;

      // Left channel — top half, centered at h/4
      const gradL = ctx.createLinearGradient(0, 0, 0, mid);
      gradL.addColorStop(0, wfA);
      gradL.addColorStop(1, wfB);
      ctx.fillStyle = gradL;
      const midL = mid / 2;
      for (let i = 0; i < track.peaksLeft.length; i++) {
        const [mn, mx] = track.peaksLeft[i];
        const yTop = midL - mx * midL * 0.88;
        const yBot = midL - mn * midL * 0.88;
        ctx.fillRect(i * barW, yTop, Math.max(1, barW - 0.5), Math.max(1, yBot - yTop));
      }

      // Right channel — bottom half, centered at h*3/4
      const gradR = ctx.createLinearGradient(0, mid, 0, h);
      gradR.addColorStop(0, wfB);
      gradR.addColorStop(1, wfA);
      ctx.fillStyle = gradR;
      const midR = mid / 2;
      for (let i = 0; i < track.peaksRight.length; i++) {
        const [mn, mx] = track.peaksRight[i];
        const yTop = mid + midR - mx * midR * 0.88;
        const yBot = mid + midR - mn * midR * 0.88;
        ctx.fillRect(i * barW, yTop, Math.max(1, barW - 0.5), Math.max(1, yBot - yTop));
      }

      // Divider line between L and R
      ctx.strokeStyle = dividerColor;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(0, mid);
      ctx.lineTo(w, mid);
      ctx.stroke();

      // Channel labels
      ctx.font = '9px -apple-system, BlinkMacSystemFont, sans-serif';
      ctx.fillStyle = cs.getPropertyValue('--text-secondary').trim() || '#7878a0';
      ctx.globalAlpha = 0.5;
      ctx.fillText('L', 4, 12);
      ctx.fillText('R', 4, mid + 12);
      ctx.globalAlpha = 1;
    } else {
      // Mono — original single-waveform rendering
      const mid = h / 2;
      const barW = w / track.peaks.length;
      const grad = ctx.createLinearGradient(0, 0, 0, h);
      grad.addColorStop(0, wfA);
      grad.addColorStop(0.5, wfB);
      grad.addColorStop(1, wfA);
      ctx.fillStyle = grad;

      for (let i = 0; i < track.peaks.length; i++) {
        const [mn, mx] = track.peaks[i];
        const yTop = mid - mx * mid * 0.92;
        const yBot = mid - mn * mid * 0.92;
        ctx.fillRect(i * barW, yTop, Math.max(1, barW - 0.5), Math.max(1, yBot - yTop));
      }

      ctx.strokeStyle = centerLine;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(0, mid);
      ctx.lineTo(w, mid);
      ctx.stroke();
    }

    ctx.restore();
  }

  /* ---- Transport ------------------------------------------------------- */

  async play() {
    if (this.loadedCount === 0) return;
    if (this.isPlaying) return;

    await this.audioContext.resume();
    if (this._iosAudio) this._iosAudio.play().catch(function() {});

    if (this.playOffset >= this.duration) this.playOffset = 0;
    this.isPlaying = true;

    // Schedule every source at the same sample-accurate future instant.
    const startAt = this.audioContext.currentTime + 0.05;
    this.playStartTime = startAt;

    for (const t of this.tracks) {
      if (!t.loaded || !t.buffer) continue;
      const src = this.audioContext.createBufferSource();
      src.buffer = t.buffer;
      src.connect(t.gainNode);
      const offset = Math.min(this.playOffset, t.buffer.duration);
      if (offset < t.buffer.duration) {
        src.start(startAt, offset);
      }
      t.source = src;
    }

    this._applyMuteState();
    this._startAnimation();
    this._requestWakeLock();

    const btn = document.getElementById('play-btn');
    btn.querySelector('.icon-play').style.display = 'none';
    btn.querySelector('.icon-pause').style.display = '';
    btn.classList.add('active');
  }

  pause() {
    if (!this.isPlaying) return;
    this.playOffset = this.currentTime();
    this.isPlaying = false;

    for (const t of this.tracks) {
      if (t.source) {
        try { t.source.stop(); } catch (_) { /* already stopped */ }
        t.source = null;
      }
    }

    this._stopAnimation();
    this._releaseWakeLock();

    const btn = document.getElementById('play-btn');
    btn.querySelector('.icon-play').style.display = '';
    btn.querySelector('.icon-pause').style.display = 'none';
    btn.classList.remove('active');
  }

  stop() {
    this.pause();
    this.playOffset = 0;
    this._updatePlayhead();
    this._updateTimeDisplay();
  }

  async seekTo(seconds) {
    const wasPlaying = this.isPlaying;
    if (wasPlaying) this.pause();
    this.playOffset = Math.max(0, Math.min(seconds, this.duration));
    this._updatePlayhead();
    this._updateTimeDisplay();
    this.currentLyricIndex = -2;
    this._updateLyrics();
    if (wasPlaying) await this.play();
  }

  currentTime() {
    if (this.isPlaying) {
      const elapsed = this.audioContext.currentTime - this.playStartTime;
      return Math.min(this.playOffset + elapsed, this.duration);
    }
    return this.playOffset;
  }

  /* ---- Mute / Solo ---------------------------------------------------- */

  toggleMute(index) {
    const t = this.tracks[index];
    t.muted = !t.muted;
    t.element.querySelector('.mute-btn').classList.toggle('active', t.muted);
    this._applyMuteState();
    this._updateTrackDimming();
  }

  toggleSolo(index) {
    const t = this.tracks[index];
    t.soloed = !t.soloed;
    t.element.querySelector('.solo-btn').classList.toggle('active', t.soloed);
    this._applyMuteState();
    this._updateTrackDimming();
  }

  _applyMuteState() {
    const anySolo = this.tracks.some(t => t.soloed);
    for (const t of this.tracks) {
      const audible = anySolo ? (t.soloed && !t.muted) : !t.muted;
      const gain = audible ? t.volume : 0;
      t.gainNode.gain.setValueAtTime(gain, this.audioContext.currentTime);
    }
  }

  _updateTrackDimming() {
    const anySolo = this.tracks.some(t => t.soloed);
    for (const t of this.tracks) {
      const active = anySolo ? (t.soloed && !t.muted) : !t.muted;
      t.element.classList.toggle('dimmed', !active);
    }
  }

  setVolume(index, value) {
    const t = this.tracks[index];
    t.volume = value;
    this._applyMuteState();
  }

  setPan(index, value) {
    const t = this.tracks[index];
    t.pan = value;
    t.panNode.pan.setValueAtTime(value, this.audioContext.currentTime);
  }

  /* ---- Animation / playhead ------------------------------------------- */

  _startAnimation() {
    const tick = () => {
      if (!this.isPlaying) return;
      const ct = this.currentTime();
      if (ct >= this.duration) {
        this.stop();
        return;
      }
      this._updatePlayhead();
      this._updateTimeDisplay();
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

  _updatePlayhead() {
    const pct = this.duration > 0 ? (this.currentTime() / this.duration) * 100 : 0;
    for (const ph of document.querySelectorAll('.playhead')) {
      ph.style.left = pct + '%';
    }
  }

  _updateTimeDisplay() {
    const el = document.getElementById('time-display');
    if (el) el.textContent = `${this._fmt(this.currentTime())} / ${this._fmt(this.duration)}`;
  }

  _fmt(s) {
    const m = Math.floor(s / 60);
    const sec = Math.floor(s % 60);
    return `${m}:${sec.toString().padStart(2, '0')}`;
  }

  /* ---- Event binding -------------------------------------------------- */

  _bindEvents() {
    // Play / Pause toggle
    document.getElementById('play-btn').addEventListener('click', () => {
      if (document.getElementById('play-btn').classList.contains('disabled')) return;
      this.isPlaying ? this.pause() : this.play();
    });

    // Stop
    document.getElementById('stop-btn').addEventListener('click', () => this.stop());

    // Mute / Solo buttons
    document.querySelectorAll('.mute-btn').forEach(btn =>
      btn.addEventListener('click', () => this.toggleMute(+btn.dataset.index))
    );
    document.querySelectorAll('.solo-btn').forEach(btn =>
      btn.addEventListener('click', () => this.toggleSolo(+btn.dataset.index))
    );

    // Volume sliders
    document.querySelectorAll('.vol-slider').forEach(slider => {
      slider.addEventListener('input', () => {
        const idx = +slider.dataset.index;
        const val = slider.value / 100;
        this.setVolume(idx, val);
        const label = document.querySelector(`.vol-value[data-index="${idx}"]`);
        if (label) label.textContent = slider.value;
      });
    });

    // Pan sliders
    document.querySelectorAll('.pan-slider').forEach(slider => {
      slider.addEventListener('input', () => {
        const idx = +slider.dataset.index;
        const val = slider.value / 100;  // -1 to +1
        this.setPan(idx, val);
        const label = document.querySelector(`.pan-value[data-index="${idx}"]`);
        if (label) {
          const v = +slider.value;
          label.textContent = v === 0 ? 'C' : (v < 0 ? `L${Math.abs(v)}` : `R${v}`);
        }
      });
      // Double-click to reset to center
      slider.addEventListener('dblclick', () => {
        slider.value = 0;
        slider.dispatchEvent(new Event('input'));
      });
    });

    // Double-click volume slider to reset to 100
    document.querySelectorAll('.vol-slider').forEach(slider => {
      slider.addEventListener('dblclick', () => {
        slider.value = 100;
        slider.dispatchEvent(new Event('input'));
      });
    });

    // Waveform click-to-seek & drag-to-scrub
    document.querySelectorAll('.waveform-container').forEach(container => {
      let dragging = false;

      const seek = (e) => {
        const rect = container.getBoundingClientRect();
        const x = Math.max(0, Math.min(e.clientX - rect.left, rect.width));
        this.seekTo((x / rect.width) * this.duration);
      };

      container.addEventListener('pointerdown', (e) => {
        dragging = true;
        container.setPointerCapture(e.pointerId);
        seek(e);
      });
      container.addEventListener('pointermove', (e) => {
        // hover line
        const rect = container.getBoundingClientRect();
        const hl = container.querySelector('.hover-line');
        if (hl) hl.style.left = (e.clientX - rect.left) + 'px';
        if (dragging) seek(e);
      });
      container.addEventListener('pointerup', () => { dragging = false; });
      container.addEventListener('pointercancel', () => { dragging = false; });
    });

    // Keyboard shortcuts
    document.addEventListener('keydown', (e) => {
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
      switch (e.code) {
        case 'Space':
          e.preventDefault();
          if (document.getElementById('play-btn').classList.contains('disabled')) return;
          this.isPlaying ? this.pause() : this.play();
          break;
        case 'ArrowLeft':
          e.preventDefault();
          this.seekTo(this.currentTime() - 5);
          break;
        case 'ArrowRight':
          e.preventDefault();
          this.seekTo(this.currentTime() + 5);
          break;
        case 'Home':
          e.preventDefault();
          this.seekTo(0);
          break;
        case 'End':
          e.preventDefault();
          this.seekTo(this.duration);
          break;
      }
    });

    // Resize → redraw waveforms
    let resizeTimer;
    const redrawAll = () => {
      for (const t of this.tracks) {
        if (t.peaks) {
          this._setupCanvas(t);
          this._drawWaveform(t);
        }
      }
    };
    window.addEventListener('resize', () => {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(redrawAll, 150);
    });

    // Theme toggle → redraw canvases with new colours
    const themeBtn = document.getElementById('theme-toggle');
    if (themeBtn) themeBtn.addEventListener('click', () => requestAnimationFrame(redrawAll));
  }
}

/* ---- iOS / mobile audio unlock ---------------------------------------- */

(function() {
  let unlocked = false;
  function unlock() {
    if (unlocked) return;
    unlocked = true;
    const s = new Audio(
      'data:audio/mp3;base64,SUQzBAAAAAAAI1RTU0UAAAAPAAADTGF2ZjU4Ljc2LjEwMAAAAAAAAAAAAAAA' +
      '//tQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAWGluZwAAAA8AAAACAAABhgC7u7u7u7u7' +
      'u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7////////////////' +
      '//////////////////////////////////////////////////////////////////8AAAAATGF2YzU4' +
      'LjEzAAAAAAAAAAAAAAAAJAAAAAAAAAAAAYYoRBqpAAAAAAD/+1DEAAABgANeAAAAIAAANIAAAAA='
    );
    s.play().then(function() { s.pause(); s.remove(); }).catch(function() {});
    document.removeEventListener('touchstart', unlock, true);
    document.removeEventListener('click', unlock, true);
  }
  document.addEventListener('touchstart', unlock, true);
  document.addEventListener('click', unlock, true);
})();

/* ---- Bootstrap -------------------------------------------------------- */

document.addEventListener('DOMContentLoaded', () => {
  const raw = document.getElementById('track-data');
  if (!raw) return;
  const trackData = JSON.parse(raw.textContent);
  window.player = new MultiTrackPlayer(SONG_NAME, trackData);
});
