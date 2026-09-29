// Auris reader — start-up. Loaded last so every reader script is defined.

// ── Init ──────────────────────────────────────────────────────────────────────

window.addEventListener('scroll', scheduleViewportProgressUpdate, { passive: true });
window.addEventListener('pagehide', () => {
  flushProgressSave({ useBeacon: true, force: true });
  _bufferGenId++;
  for (const controller of _ttsAbortControllers.values()) controller.abort();
  _ttsAbortControllers.clear();
  navigator.sendBeacon('/api/tts/cancel');
});
window.addEventListener('beforeunload', () => flushProgressSave({ useBeacon: true, force: true }));
document.addEventListener('visibilitychange', () => {
  if (document.hidden) {
    flushProgressSave({ useBeacon: true, force: true });
  }
});

_audioA.addEventListener('timeupdate', updateRemainingTime);
_audioB.addEventListener('timeupdate', updateRemainingTime);
setTOCOpen(!window.matchMedia('(max-width: 768px)').matches);
applySpeakerLabelPreference();
applyExportPreset(document.getElementById('export-preset').value);
initMediaSession();

loadTOC();

function persistTimedProgress() {
  if (_loadedSegIdx !== currentSegIdx || !currentChapterId) return;
  if (Date.now() - _progressTick >= 5000) {
    _progressTick = Date.now();
    sendProgress(currentChapterId, currentSegIdx);
  }
}
_audioA.addEventListener('timeupdate', persistTimedProgress);
_audioB.addEventListener('timeupdate', persistTimedProgress);
