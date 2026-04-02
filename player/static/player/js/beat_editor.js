/**
 * BeatEditor – interactive canvas-based beat marker editor.
 *
 * Renders a waveform with vertical beat markers that can be dragged,
 * added (click), and removed (right-click).
 */

function BeatEditor(canvas, peaks, duration, beats) {
  'use strict';

  this.canvas = canvas;
  this.ctx = canvas.getContext('2d');
  this.peaks = peaks || [];
  this.duration = duration || 1;
  this.beats = (beats || []).slice().sort(function(a, b) { return a - b; });
  this.onChange = null;

  this._dragIdx = -1;
  this._dragStartX = 0;
  this._hoverTime = -1;

  this._viewStart = 0;
  this._viewEnd = this.duration;

  var self = this;

  this._resize();
  window.addEventListener('resize', function() { self._resize(); self.draw(); });

  canvas.addEventListener('mousedown', function(e) { self._onMouseDown(e); });
  canvas.addEventListener('mousemove', function(e) { self._onMouseMove(e); });
  canvas.addEventListener('mouseup', function(e) { self._onMouseUp(e); });
  canvas.addEventListener('contextmenu', function(e) {
    e.preventDefault();
    self._onRightClick(e);
  });
  canvas.addEventListener('wheel', function(e) {
    e.preventDefault();
    self._onWheel(e);
  }, { passive: false });

  this.draw();
}

BeatEditor.prototype._resize = function() {
  var rect = this.canvas.parentElement.getBoundingClientRect();
  var dpr = window.devicePixelRatio || 1;
  this.canvas.width = rect.width * dpr;
  this.canvas.height = 200 * dpr;
  this.canvas.style.width = rect.width + 'px';
  this.canvas.style.height = '200px';
  this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  this._w = rect.width;
  this._h = 200;
};

BeatEditor.prototype._timeToX = function(t) {
  return ((t - this._viewStart) / (this._viewEnd - this._viewStart)) * this._w;
};

BeatEditor.prototype._xToTime = function(x) {
  return this._viewStart + (x / this._w) * (this._viewEnd - this._viewStart);
};

BeatEditor.prototype.draw = function() {
  var ctx = this.ctx;
  var w = this._w;
  var h = this._h;
  var peaks = this.peaks;

  ctx.clearRect(0, 0, w, h);

  // Waveform
  var style = getComputedStyle(document.documentElement);
  var wfColor = style.getPropertyValue('--accent-blue').trim() || '#4a9eff';
  ctx.fillStyle = wfColor;
  ctx.globalAlpha = 0.4;

  var viewFraction = (this._viewEnd - this._viewStart) / this.duration;
  var startPeakIdx = Math.floor((this._viewStart / this.duration) * peaks.length);
  var endPeakIdx = Math.ceil((this._viewEnd / this.duration) * peaks.length);
  var visiblePeaks = peaks.slice(Math.max(0, startPeakIdx), Math.min(peaks.length, endPeakIdx));
  var mid = h / 2;

  if (visiblePeaks.length > 0) {
    var barW = w / visiblePeaks.length;
    for (var i = 0; i < visiblePeaks.length; i++) {
      var amp = visiblePeaks[i] * mid * 0.9;
      ctx.fillRect(i * barW, mid - amp, Math.max(1, barW - 0.5), amp * 2);
    }
  }

  ctx.globalAlpha = 1;

  // Beat markers
  for (var j = 0; j < this.beats.length; j++) {
    var bx = this._timeToX(this.beats[j]);
    if (bx < -2 || bx > w + 2) continue;

    var isAccent = j % 4 === 0;
    ctx.strokeStyle = isAccent ? '#ffcc00' : 'rgba(255,255,255,0.5)';
    ctx.lineWidth = isAccent ? 2 : 1;
    ctx.beginPath();
    ctx.moveTo(bx, 0);
    ctx.lineTo(bx, h);
    ctx.stroke();

    if (isAccent) {
      ctx.fillStyle = '#ffcc00';
      ctx.font = '10px sans-serif';
      ctx.fillText(String(j + 1), bx + 3, 12);
    }
  }

  // Hover indicator
  if (this._hoverTime >= 0) {
    var hx = this._timeToX(this._hoverTime);
    ctx.strokeStyle = 'rgba(255,255,255,0.25)';
    ctx.lineWidth = 1;
    ctx.setLineDash([4, 4]);
    ctx.beginPath();
    ctx.moveTo(hx, 0);
    ctx.lineTo(hx, h);
    ctx.stroke();
    ctx.setLineDash([]);
  }
};

BeatEditor.prototype._findNearestBeat = function(x, threshold) {
  threshold = threshold || 8;
  var best = -1;
  var bestDist = Infinity;
  for (var i = 0; i < this.beats.length; i++) {
    var bx = this._timeToX(this.beats[i]);
    var dist = Math.abs(bx - x);
    if (dist < bestDist && dist < threshold) {
      bestDist = dist;
      best = i;
    }
  }
  return best;
};

BeatEditor.prototype._onMouseDown = function(e) {
  var rect = this.canvas.getBoundingClientRect();
  var x = e.clientX - rect.left;
  var idx = this._findNearestBeat(x);

  if (idx >= 0) {
    this._dragIdx = idx;
    this._dragStartX = x;
    this.canvas.style.cursor = 'grabbing';
  } else {
    var t = this._xToTime(x);
    if (t >= 0 && t <= this.duration) {
      this.beats.push(t);
      this.beats.sort(function(a, b) { return a - b; });
      this._emitChange();
      this.draw();
    }
  }
};

BeatEditor.prototype._onMouseMove = function(e) {
  var rect = this.canvas.getBoundingClientRect();
  var x = e.clientX - rect.left;

  if (this._dragIdx >= 0) {
    var t = this._xToTime(x);
    t = Math.max(0, Math.min(this.duration, t));
    this.beats[this._dragIdx] = t;
    this.draw();
  } else {
    var near = this._findNearestBeat(x);
    this.canvas.style.cursor = near >= 0 ? 'grab' : 'crosshair';
    this._hoverTime = near < 0 ? this._xToTime(x) : -1;
    this.draw();
  }
};

BeatEditor.prototype._onMouseUp = function(e) {
  if (this._dragIdx >= 0) {
    this.beats.sort(function(a, b) { return a - b; });
    this._emitChange();
    this._dragIdx = -1;
    this.canvas.style.cursor = 'crosshair';
    this.draw();
  }
};

BeatEditor.prototype._onRightClick = function(e) {
  var rect = this.canvas.getBoundingClientRect();
  var x = e.clientX - rect.left;
  var idx = this._findNearestBeat(x, 12);
  if (idx >= 0) {
    this.beats.splice(idx, 1);
    this._emitChange();
    this.draw();
  }
};

BeatEditor.prototype._onWheel = function(e) {
  var rect = this.canvas.getBoundingClientRect();
  var mouseX = e.clientX - rect.left;
  var mouseTime = this._xToTime(mouseX);
  var viewSpan = this._viewEnd - this._viewStart;

  var zoomFactor = e.deltaY > 0 ? 1.15 : 0.87;
  var newSpan = Math.max(1, Math.min(this.duration, viewSpan * zoomFactor));

  var ratio = (mouseTime - this._viewStart) / viewSpan;
  this._viewStart = mouseTime - ratio * newSpan;
  this._viewEnd = this._viewStart + newSpan;

  if (this._viewStart < 0) {
    this._viewEnd -= this._viewStart;
    this._viewStart = 0;
  }
  if (this._viewEnd > this.duration) {
    this._viewStart -= (this._viewEnd - this.duration);
    this._viewEnd = this.duration;
    if (this._viewStart < 0) this._viewStart = 0;
  }

  this.draw();
};

BeatEditor.prototype._emitChange = function() {
  if (this.onChange) {
    this.onChange(this.beats.slice());
  }
};
