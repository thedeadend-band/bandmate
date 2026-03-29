/* ========================================================================
   BandMate – Setlist player (sequential master-track playback)
   ======================================================================== */

const PP_ICONS = {
  tempo: '<svg class="info-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2L4 20h16L12 2z"/><line x1="12" y1="8" x2="17" y2="3"/></svg>',
  key: '<svg class="info-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 2v13.6a4 4 0 104 4V2"/><line x1="6" y1="8" x2="16" y2="6"/><line x1="6" y1="12" x2="16" y2="10"/></svg>',
  mic: '<svg class="info-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 1a4 4 0 00-4 4v4a4 4 0 008 0V5a4 4 0 00-4-4z"/><path d="M6 11v1a6 6 0 0012 0v-1"/><line x1="12" y1="18" x2="12" y2="23"/><line x1="8" y1="23" x2="16" y2="23"/></svg>',
  electric: '<svg class="info-icon-sm" viewBox="0 0 24 24" fill="currentColor"><path d="M7 2v11h3v9l7-12h-4l3-8z"/></svg>',
  acoustic: '<svg class="info-icon-sm" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 1v7"/><path d="M10 1h4"/><path d="M11 8h2"/><path d="M12 8c-1 0-3 1.5-4 3.5s-1.5 3-.5 5c.7 1.4 1 2.5.5 3.5-.3.6 0 1.5.8 2.2a3.5 3.5 0 004.4 0c.8-.7 1.1-1.6.8-2.2-.5-1-.2-2.1.5-3.5 1-2 .5-3.5-.5-5S13 8 12 8z"/><circle cx="12" cy="16" r="1.5"/></svg>',
  capo: '<svg class="info-icon-sm" viewBox="0 0 24 24" fill="currentColor"><path d="M18 2c-1 0-2 1-3 3l-1 2c-.5 1-1.5 2-3 2H8c-2 0-3.5 2-4 4-.5 2 0 3 1 4s2.5 1 4 .5c1.5-.5 2.5-1.5 3-3l1-2c.5-1 1-2 2-3l2-3c.5-1 1-2 1-3s-.5-1.5-1-1.5z"/></svg>',
  tuning: '<svg class="info-icon-sm" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="4" width="20" height="16" rx="3"/><path d="M5 16a7 7 0 0114 0"/><line x1="12" y1="16" x2="8.5" y2="9"/></svg>',
};

class SetlistPlayer {
  constructor(items) {
    this.allItems = items;
    this.songs = items.filter(it => !it.is_break);
    this.audioContext = null;
    this.currentIndex = -1;
    this.buffer = null;
    this.source = null;
    this.gainNode = null;
    this.isPlaying = false;
    this.playStartTime = 0;
    this.playOffset = 0;
    this.duration = 0;
    this.peaks = null;
    this.animationId = null;
    this.dpr = 1;
    this.displayWidth = 0;
    this.displayHeight = 0;

    this.songInfo = null;
    this.lyrics = null;
    this.lyricOffset = 0;
    this.currentLyricIndex = -1;

    this._wakeLock = null;

    this._initAudio();
    this._bindEvents();
    this._preloadDurations();
    if (this.songs.length > 0) this.loadSong(0);

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

  _initAudio() {
    this.audioContext = new (window.AudioContext || window.webkitAudioContext)();

    const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent) ||
      (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);

    if (isIOS) {
      try {
        this._streamDest = this.audioContext.createMediaStreamDestination();
        this._iosAudio = document.createElement('audio');
        this._iosAudio.setAttribute('playsinline', '');
        this._iosAudio.srcObject = this._streamDest.stream;
        this.outputNode = this._streamDest;
      } catch (e) {
        this.outputNode = this.audioContext.destination;
      }
    } else {
      this.outputNode = this.audioContext.destination;
    }

    this.gainNode = this.audioContext.createGain();
    this.gainNode.connect(this.outputNode);
  }

  /* ---- Loading -------------------------------------------------------- */

  async loadSong(index) {
    if (index < 0 || index >= this.songs.length) return;
    const wasPlaying = this.isPlaying;
    if (this.isPlaying) this.pause();

    this.playOffset = 0;
    this.currentIndex = index;
    this.currentLyricIndex = -1;
    this._highlightRow(index);

    const song = this.songs[index];
    const overlay = document.getElementById('pp-loading');
    const label = overlay.querySelector('span');
    overlay.classList.remove('hidden');

    document.getElementById('pp-now-label').textContent = song.name;

    try {
      const infoPromise = fetch(`/api/songs/${encodeURIComponent(song.name)}/info/`)
        .then(r => r.ok ? r.json() : null).catch(() => null);

      label.textContent = 'Loading waveform\u2026';
      const wfUrl = `/api/songs/${encodeURIComponent(song.name)}/master/waveform/`;
      const wfResp = await fetch(wfUrl);
      if (!wfResp.ok) throw new Error(`Waveform ${wfResp.status}`);
      const wfData = await wfResp.json();
      this.channels = wfData.channels || 1;
      if (this.channels >= 2) {
        this.peaksLeft = wfData.peaks_left;
        this.peaksRight = wfData.peaks_right;
        this.peaks = wfData.peaks_left;
      } else {
        this.peaks = wfData.peaks;
        this.peaksLeft = null;
        this.peaksRight = null;
      }
      this.duration = wfData.duration;
      this._setupCanvas();
      this._drawWaveform();
      this._updateTime();

      const infoData = await infoPromise;
      this.songInfo = infoData?.info || null;
      this.lyrics = infoData?.lyrics || null;
      this.lyricOffset = this.songInfo?.lyric_offset_secs || 0;
      this._renderInfoPanel();
      this._renderLyrics();
      this.currentLyricIndex = -2;
      this._updateLyrics();

      if (this.songInfo?.artist && this.songInfo?.title) {
        document.getElementById('pp-now-label').textContent =
          `${this.songInfo.artist} \u2013 ${this.songInfo.title}`;
      }

      label.textContent = 'Loading audio\u2026';
      const audioUrl = `/api/songs/${encodeURIComponent(song.name)}/master/audio/`;
      const audioResp = await fetch(audioUrl);
      if (!audioResp.ok) throw new Error(`Audio ${audioResp.status}`);
      const arrayBuf = await audioResp.arrayBuffer();
      this.buffer = await this.audioContext.decodeAudioData(arrayBuf);

      overlay.classList.add('hidden');
      document.getElementById('pp-play').classList.remove('disabled');
      this._updatePlayhead();

      if (wasPlaying) await this.play();
    } catch (err) {
      console.error(`Failed to load "${song.name}":`, err);
      label.textContent = 'Load failed';
      label.style.color = getComputedStyle(document.documentElement).getPropertyValue('--accent-red');
    }
  }

  async _preloadDurations() {
    for (let i = 0; i < this.songs.length; i++) {
      try {
        const url = `/api/songs/${encodeURIComponent(this.songs[i].name)}/master/waveform/`;
        const resp = await fetch(url);
        if (!resp.ok) continue;
        const data = await resp.json();
        const el = document.querySelector(`.pp-track-dur[data-index="${this._domIndexForSong(i)}"]`);
        if (el) el.textContent = this._fmt(data.duration);
      } catch (_) { /* ignore */ }
    }
  }

  _domIndexForSong(songIndex) {
    let songCount = 0;
    for (let i = 0; i < this.allItems.length; i++) {
      if (!this.allItems[i].is_break) {
        if (songCount === songIndex) return i;
        songCount++;
      }
    }
    return -1;
  }

  _songIndexFromDom(domIndex) {
    if (this.allItems[domIndex]?.is_break) return -1;
    let songCount = 0;
    for (let i = 0; i < domIndex; i++) {
      if (!this.allItems[i].is_break) songCount++;
    }
    return songCount;
  }

  /* ---- Info panel (dynamic) ------------------------------------------- */

  _renderInfoPanel() {
    const panel = document.getElementById('pp-info-panel');
    const info = this.songInfo;
    if (!info) { panel.style.display = 'none'; return; }

    let html = '';

    if (info.tempo || info.key) {
      html += '<div class="info-row">';
      if (info.tempo) html += `<span class="info-chip">${PP_ICONS.tempo} ${info.tempo} BPM</span>`;
      if (info.key) html += `<span class="info-chip">${PP_ICONS.key} ${info.key}</span>`;
      html += '</div>';
    }

    if (info.vocals) {
      html += '<div class="info-row">';
      if (info.vocals.lead?.length) {
        const initials = info.vocals.lead.map(n => n[0]).join(', ');
        html += `<span class="info-chip vocals-lead">${PP_ICONS.mic} ${initials}</span>`;
      }
      if (info.vocals.backing?.length) {
        const initials = info.vocals.backing.map(n => n[0]).join(', ');
        html += `<span class="info-chip vocals-backing">${PP_ICONS.mic} ${initials}</span>`;
      }
      html += '</div>';
    }

    if (info.guitars) {
      const gTypes = [
        ['lead_guitar', 'Lead Guitar'],
        ['rhythm_guitar', 'Rhythm Guitar'],
        ['bass_guitar', 'Bass Guitar'],
      ];
      html += '<div class="guitar-rows">';
      for (const [key, label] of gTypes) {
        const g = info.guitars[key];
        if (!g) continue;
        const typeIcon = g.type === 'acoustic' ? PP_ICONS.acoustic : PP_ICONS.electric;
        let row = `<div class="guitar-row"><span class="guitar-label">${label}</span>`;
        row += `<span class="guitar-detail">${typeIcon}</span>`;
        row += `<span class="guitar-detail">${PP_ICONS.tuning} ${g.tuning}</span>`;
        if (g.capo) row += `<span class="guitar-detail">${PP_ICONS.capo} ${g.capo}</span>`;
        row += '</div>';
        html += row;
      }
      html += '</div>';
    }

    panel.innerHTML = html;
    panel.style.display = html ? '' : 'none';
  }

  /* ---- Lyrics --------------------------------------------------------- */

  _renderLyrics() {
    const container = document.getElementById('lyrics-container');
    const scroller = document.getElementById('lyrics-scroller');
    const divider = document.getElementById('pp-lyrics-divider');
    if (!this.lyrics || !this.lyrics.length) {
      container.classList.remove('lyrics-fullscreen');
      container.style.display = 'none';
      if (divider) divider.style.display = 'none';
      scroller.innerHTML = '';
      return;
    }
    container.style.display = '';
    if (divider) divider.style.display = '';
    scroller.style.transform = '';
    scroller.innerHTML = this.lyrics.map(l =>
      `<div class="lyrics-line" data-time="${l.time}">${l.text || '&nbsp;'}</div>`
    ).join('');
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

  /* ---- Canvas / waveform ---------------------------------------------- */

  _setupCanvas() {
    const canvas = document.getElementById('pp-canvas');
    const container = canvas.parentElement;
    const rect = container.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    this.dpr = dpr;
    this.displayWidth = rect.width;
    this.displayHeight = rect.height;
  }

  _drawWaveform() {
    if (!this.peaks || !this.peaks.length) return;
    const canvas = document.getElementById('pp-canvas');
    const ctx = canvas.getContext('2d');
    const dpr = this.dpr;
    const w = this.displayWidth;
    const h = this.displayHeight;
    const cs = getComputedStyle(document.documentElement);
    const wfA = cs.getPropertyValue('--wf-color-a').trim() || '#4a9eff';
    const wfB = cs.getPropertyValue('--wf-color-b').trim() || '#7c5cfc';
    const centerLine = cs.getPropertyValue('--wf-center-line').trim() || 'rgba(255,255,255,0.07)';
    const dividerColor = cs.getPropertyValue('--wf-divider').trim() || centerLine;

    ctx.save();
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, w, h);

    if (this.channels >= 2 && this.peaksLeft && this.peaksRight) {
      const mid = h / 2;
      const barW = w / this.peaksLeft.length;

      const gradL = ctx.createLinearGradient(0, 0, 0, mid);
      gradL.addColorStop(0, wfA);
      gradL.addColorStop(1, wfB);
      ctx.fillStyle = gradL;
      const midL = mid / 2;
      for (let i = 0; i < this.peaksLeft.length; i++) {
        const [mn, mx] = this.peaksLeft[i];
        const yTop = midL - mx * midL * 0.88;
        const yBot = midL - mn * midL * 0.88;
        ctx.fillRect(i * barW, yTop, Math.max(1, barW - 0.5), Math.max(1, yBot - yTop));
      }

      const gradR = ctx.createLinearGradient(0, mid, 0, h);
      gradR.addColorStop(0, wfB);
      gradR.addColorStop(1, wfA);
      ctx.fillStyle = gradR;
      const midR = mid / 2;
      for (let i = 0; i < this.peaksRight.length; i++) {
        const [mn, mx] = this.peaksRight[i];
        const yTop = mid + midR - mx * midR * 0.88;
        const yBot = mid + midR - mn * midR * 0.88;
        ctx.fillRect(i * barW, yTop, Math.max(1, barW - 0.5), Math.max(1, yBot - yTop));
      }

      ctx.strokeStyle = dividerColor;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(0, mid);
      ctx.lineTo(w, mid);
      ctx.stroke();

      ctx.font = '9px -apple-system, BlinkMacSystemFont, sans-serif';
      ctx.fillStyle = cs.getPropertyValue('--text-secondary').trim() || '#7878a0';
      ctx.globalAlpha = 0.5;
      ctx.fillText('L', 4, 12);
      ctx.fillText('R', 4, mid + 12);
      ctx.globalAlpha = 1;
    } else {
      const mid = h / 2;
      const barW = w / this.peaks.length;
      const grad = ctx.createLinearGradient(0, 0, 0, h);
      grad.addColorStop(0, wfA);
      grad.addColorStop(0.5, wfB);
      grad.addColorStop(1, wfA);
      ctx.fillStyle = grad;
      for (let i = 0; i < this.peaks.length; i++) {
        const [mn, mx] = this.peaks[i];
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
    if (!this.buffer) return;
    if (this.isPlaying) return;
    await this.audioContext.resume();
    if (this._iosAudio) this._iosAudio.play().catch(function() {});
    if (this.playOffset >= this.duration) this.playOffset = 0;
    this.isPlaying = true;

    const startAt = this.audioContext.currentTime + 0.05;
    this.playStartTime = startAt;
    const src = this.audioContext.createBufferSource();
    src.buffer = this.buffer;
    src.connect(this.gainNode);
    src.start(startAt, this.playOffset);
    src.onended = () => {
      if (this.isPlaying && this.currentTime() >= this.duration - 0.1) {
        this._autoAdvance();
      }
    };
    this.source = src;
    this._startAnimation();
    this._requestWakeLock();
    this._showPause(true);
  }

  pause() {
    if (!this.isPlaying) return;
    this.playOffset = this.currentTime();
    this.isPlaying = false;
    if (this.source) {
      try { this.source.stop(); } catch (_) {}
      this.source = null;
    }
    if (this._iosAudio) this._iosAudio.pause();
    this._stopAnimation();
    this._releaseWakeLock();
    this._showPause(false);
  }

  stop() {
    this.pause();
    this.playOffset = 0;
    this._updatePlayhead();
    this._updateTime();
  }

  async next() {
    if (this.currentIndex < this.songs.length - 1) {
      const wasPlaying = this.isPlaying;
      this.pause();
      await this.loadSong(this.currentIndex + 1);
      if (wasPlaying) await this.play();
    }
  }

  async _autoAdvance() {
    this.pause();
    this.playOffset = 0;
    this._updatePlayhead();
    this._updateTime();
    if (this.currentIndex < this.songs.length - 1) {
      await this.loadSong(this.currentIndex + 1);
      await this.play();
    }
  }

  async prev() {
    if (this.currentTime() > 3) {
      this.seekTo(0);
    } else if (this.currentIndex > 0) {
      const wasPlaying = this.isPlaying;
      this.pause();
      await this.loadSong(this.currentIndex - 1);
      if (wasPlaying) await this.play();
    } else {
      this.seekTo(0);
    }
  }

  async seekTo(seconds) {
    const wasPlaying = this.isPlaying;
    if (wasPlaying) this.pause();
    this.playOffset = Math.max(0, Math.min(seconds, this.duration));
    this._updatePlayhead();
    this._updateTime();
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

  /* ---- Animation ------------------------------------------------------ */

  _startAnimation() {
    const tick = () => {
      if (!this.isPlaying) return;
      this._updatePlayhead();
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

  _updatePlayhead() {
    const pct = this.duration > 0 ? (this.currentTime() / this.duration) * 100 : 0;
    document.getElementById('pp-playhead').style.left = pct + '%';
  }

  _updateTime() {
    document.getElementById('pp-time').textContent =
      `${this._fmt(this.currentTime())} / ${this._fmt(this.duration)}`;
  }

  _showPause(show) {
    const btn = document.getElementById('pp-play');
    btn.querySelector('.icon-play').style.display = show ? 'none' : '';
    btn.querySelector('.icon-pause').style.display = show ? '' : 'none';
    btn.classList.toggle('active', show);
  }

  _highlightRow(index) {
    const domIdx = this._domIndexForSong(index);
    document.querySelectorAll('.pp-track-row').forEach(el => {
      el.classList.toggle('pp-active', +el.dataset.index === domIdx);
    });
  }

  _fmt(s) {
    if (!s || isNaN(s)) return '0:00';
    const m = Math.floor(s / 60);
    const sec = Math.floor(s % 60);
    return `${m}:${sec.toString().padStart(2, '0')}`;
  }

  /* ---- Events --------------------------------------------------------- */

  _toggleLyricsFullscreen() {
    const container = document.getElementById('lyrics-container');
    if (!container) return;
    const isFullscreen = container.classList.toggle('lyrics-fullscreen');
    const btn = document.getElementById('lyrics-fullscreen-btn');
    if (btn) {
      btn.innerHTML = isFullscreen
        ? '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="4 14 10 14 10 20"/><polyline points="20 10 14 10 14 4"/><line x1="14" y1="10" x2="21" y2="3"/><line x1="3" y1="21" x2="10" y2="14"/></svg>'
        : '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 3 21 3 21 9"/><polyline points="9 21 3 21 3 15"/><line x1="21" y1="3" x2="14" y2="10"/><line x1="3" y1="21" x2="10" y2="14"/></svg>';
    }
    this.currentLyricIndex = -2;
    this._updateLyrics();
  }

  _initLyricsScrub() {
    const container = document.getElementById('lyrics-container');
    const scroller = document.getElementById('lyrics-scroller');
    if (!container || !scroller) return;

    let scrubbing = false;
    let wasPlaying = false;
    let startY = 0;
    let startTranslate = 0;

    const getCurrentTranslateY = () => {
      const m = scroller.style.transform.match(/translateY\((.+?)px\)/);
      return m ? parseFloat(m[1]) : 0;
    };

    const findCenteredLineIndex = () => {
      const lines = scroller.querySelectorAll('.lyrics-line');
      if (!lines.length) return -1;
      const centerY = container.getBoundingClientRect().top + container.clientHeight / 2;
      let closest = 0;
      let closestDist = Infinity;
      lines.forEach((el, i) => {
        const rect = el.getBoundingClientRect();
        const mid = rect.top + rect.height / 2;
        const dist = Math.abs(mid - centerY);
        if (dist < closestDist) { closestDist = dist; closest = i; }
      });
      return closest;
    };

    const onDown = (e) => {
      if (!container.classList.contains('lyrics-fullscreen')) return;
      if (e.target.closest('.lyrics-fullscreen-btn') || e.target.closest('.lyrics-title')) return;
      scrubbing = true;
      wasPlaying = this.isPlaying;
      if (wasPlaying) this.pause();
      startY = e.clientY || e.touches?.[0]?.clientY || 0;
      startTranslate = getCurrentTranslateY();
      scroller.style.transition = 'none';
      container.classList.add('lyrics-scrubbing');
      container.setPointerCapture?.(e.pointerId);
    };

    const onMove = (e) => {
      if (!scrubbing) return;
      const y = e.clientY || e.touches?.[0]?.clientY || 0;
      const delta = y - startY;
      scroller.style.transform = `translateY(${startTranslate + delta}px)`;
    };

    const onUp = () => {
      if (!scrubbing) return;
      scrubbing = false;
      scroller.style.transition = '';
      container.classList.remove('lyrics-scrubbing');
      const idx = findCenteredLineIndex();
      if (idx >= 0 && this.lyrics && this.lyrics[idx]) {
        const time = this.lyrics[idx].time + (this.lyricOffset || 0);
        this.seekTo(Math.max(0, time));
      }
      if (wasPlaying) this.play();
    };

    container.addEventListener('pointerdown', onDown);
    container.addEventListener('pointermove', onMove);
    container.addEventListener('pointerup', onUp);
    container.addEventListener('pointercancel', onUp);
  }

  _bindEvents() {
    document.getElementById('pp-play').addEventListener('click', () => {
      if (document.getElementById('pp-play').classList.contains('disabled')) return;
      this.isPlaying ? this.pause() : this.play();
    });
    document.getElementById('pp-stop').addEventListener('click', () => this.stop());
    document.getElementById('pp-next').addEventListener('click', () => this.next());
    document.getElementById('pp-prev').addEventListener('click', () => this.prev());

    const fsBtn = document.getElementById('lyrics-fullscreen-btn');
    if (fsBtn) {
      fsBtn.addEventListener('click', () => this._toggleLyricsFullscreen());
    }

    this._initLyricsScrub();

    document.getElementById('pp-tracklist').addEventListener('click', (e) => {
      const row = e.target.closest('.pp-track-row');
      if (row) {
        const domIdx = +row.dataset.index;
        const songIdx = this._songIndexFromDom(domIdx);
        if (songIdx < 0) return;
        const wasPlaying = this.isPlaying;
        this.pause();
        this.loadSong(songIdx).then(() => { if (wasPlaying) this.play(); });
      }
    });

    const container = document.getElementById('pp-waveform-container');
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
      const rect = container.getBoundingClientRect();
      const hl = container.querySelector('.hover-line');
      if (hl) hl.style.left = (e.clientX - rect.left) + 'px';
      if (dragging) seek(e);
    });
    container.addEventListener('pointerup', () => { dragging = false; });
    container.addEventListener('pointercancel', () => { dragging = false; });

    document.addEventListener('keydown', (e) => {
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
      switch (e.code) {
        case 'Space':
          e.preventDefault();
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
        case 'KeyN':
          this.next();
          break;
        case 'KeyP':
          this.prev();
          break;
      }
    });

    let resizeTimer;
    const redraw = () => {
      if (this.peaks) {
        this._setupCanvas();
        this._drawWaveform();
      }
    };
    window.addEventListener('resize', () => {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(redraw, 150);
    });

    const themeBtn = document.getElementById('theme-toggle');
    if (themeBtn) themeBtn.addEventListener('click', () => requestAnimationFrame(redraw));
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
      'u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7u7////////////////' +
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
  const raw = document.getElementById('pp-data');
  if (!raw) return;
  const items = JSON.parse(raw.textContent);
  window.setlistPlayer = new SetlistPlayer(items);
});
