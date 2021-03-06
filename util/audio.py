import librosa
import librosa.filters
import math
import numpy as np
import tensorflow as tf
from scipy import signal
from hparams import hparams


def load_wav(path):
  return librosa.core.load(path, sr=hparams.sample_rate)[0]


def save_wav(wav, path):
  wav *= 32767 / max(0.01, np.max(np.abs(wav)))
  librosa.output.write_wav(path, wav.astype(np.float32), hparams.sample_rate)


def trim_silence(wav):
  '''Trim leading and trailing silence

  Useful for M-AILABS dataset if we choose to trim the extra 0.5 silences.
  '''
  _, hop_length, win_length = _stft_parameters()
  return librosa.effects.trim(wav, frame_length=win_length, hop_length=hop_length)[0]


def spectrogram(y):
  D = _stft(y)
  S = _amp_to_db(np.abs(D)) - hparams.ref_level_db
  return _normalize(S)


def inv_spectrogram(spectrogram):
  '''Converts spectrogram to waveform using librosa'''
  S = _db_to_amp(_denormalize(spectrogram) + hparams.ref_level_db)  # Convert back to linear
  return _griffin_lim(S ** hparams.power)          # Reconstruct phase


def inv_spectrogram_tensorflow(spectrogram):
  '''Builds computational graph to convert spectrogram to waveform using TensorFlow.

  Unlike inv_spectrogram, this does NOT invert the preemphasis. The caller should call
  inv_preemphasis on the output after running the graph.
  '''
  S = _db_to_amp_tensorflow(_denormalize_tensorflow(spectrogram) + hparams.ref_level_db)
  return _griffin_lim_tensorflow(tf.pow(S, hparams.power))


def melspectrogram(y):
  D = _stft(y)
  S = _amp_to_db(_linear_to_mel(np.abs(D)))
  return _normalize(S)


def find_endpoint(wav, threshold_db=-40, min_silence_sec=0.8):
  window_length = int(hparams.sample_rate * min_silence_sec)
  hop_length = int(window_length / 4)
  threshold = _db_to_amp(threshold_db)
  for x in range(hop_length, len(wav) - window_length, hop_length):
    if np.max(wav[x:x+window_length]) < threshold:
      return x + hop_length
  return len(wav)


def _griffin_lim(S):
  '''librosa implementation of Griffin-Lim
  Based on https://github.com/librosa/librosa/issues/434
  '''
  angles = np.exp(2j * np.pi * np.random.rand(*S.shape))
  S_complex = np.abs(S).astype(np.complex)
  y = _istft(S_complex * angles)
  for i in range(hparams.griffin_lim_iters):
    angles = np.exp(1j * np.angle(_stft(y)))
    y = _istft(S_complex * angles)
  return y


def _griffin_lim_tensorflow(S):
  '''TensorFlow implementation of Griffin-Lim
  Based on https://github.com/Kyubyong/tensorflow-exercises/blob/master/Audio_Processing.ipynb
  '''
  with tf.variable_scope('griffinlim'):
    # TensorFlow's stft and istft operate on a batch of spectrograms; create batch of size 1
    S = tf.expand_dims(S, 0)
    S_complex = tf.identity(tf.cast(S, dtype=tf.complex64))
    y = _istft_tensorflow(S_complex)
    for i in range(hparams.griffin_lim_iters):
      est = _stft_tensorflow(y)
      angles = est / tf.cast(tf.maximum(1e-8, tf.abs(est)), tf.complex64)
      y = _istft_tensorflow(S_complex * angles)
    return tf.squeeze(y, 0)


def _stft(y):
  n_fft, hop_length, win_length = _stft_parameters()
  return librosa.stft(y=y, n_fft=n_fft, hop_length=hop_length, win_length=win_length)


def _istft(y):
  _, hop_length, win_length = _stft_parameters()
  return librosa.istft(y, hop_length=hop_length, win_length=win_length)


def _stft_tensorflow(signals):
  n_fft, hop_length, win_length = _stft_parameters()
  return tf.contrib.signal.stft(signals, win_length, hop_length, n_fft, pad_end=False)


def _istft_tensorflow(stfts):
  n_fft, hop_length, win_length = _stft_parameters()
  return tf.contrib.signal.inverse_stft(stfts, win_length, hop_length, n_fft)


def _stft_parameters():
  n_fft = (hparams.num_freq - 1) * 2
  hop_length = int(hparams.frame_shift_ms / 1000 * hparams.sample_rate)
  win_length = int(hparams.frame_length_ms / 1000 * hparams.sample_rate)
  return n_fft, hop_length, win_length


# Conversions:

_mel_basis = None


def _linear_to_mel(spectrogram):
  global _mel_basis
  if _mel_basis is None:
    _mel_basis = _build_mel_basis()
  return np.dot(_mel_basis, spectrogram)


def _build_mel_basis():
  n_fft = (hparams.num_freq - 1) * 2
  return librosa.filters.mel(hparams.sample_rate, n_fft, n_mels=hparams.num_mels,
    fmin=hparams.min_mel_freq, fmax=hparams.max_mel_freq)


def _amp_to_db(x):
  return 20 * np.log10(np.maximum(1e-5, x))


def _db_to_amp(x):
  return np.power(10.0, x * 0.05)


def _db_to_amp_tensorflow(x):
  return tf.pow(tf.ones(tf.shape(x)) * 10.0, x * 0.05)


def _normalize(S):
  if hparams.symmetric_mels:
    return np.clip((2 * hparams.max_abs_value) * ((S - hparams.min_level_db) / (-hparams.min_level_db)) - hparams.max_abs_value,
     -hparams.max_abs_value, hparams.max_abs_value)
  else:
    return np.clip(hparams.max_abs_value * ((S - hparams.min_level_db) / (-hparams.min_level_db)), 0, hparams.max_abs_value)


def _denormalize(D):
  if hparams.symmetric_mels:
    return (((np.clip(D, -hparams.max_abs_value,
      hparams.max_abs_value) + hparams.max_abs_value) * -hparams.min_level_db / (2 * hparams.max_abs_value))
      + hparams.min_level_db)
  else:
    return ((np.clip(D, 0, hparams.max_abs_value) * -hparams.min_level_db / hparams.max_abs_value) + hparams.min_level_db)


def _denormalize_tensorflow(D):
  if hparams.symmetric_mels:
    return (((tf.clip_by_value(D, -hparams.max_abs_value,
      hparams.max_abs_value) + hparams.max_abs_value) * -hparams.min_level_db / (2 * hparams.max_abs_value))
      + hparams.min_level_db)
  else:
    return ((tf.clip_by_value(D, 0, hparams.max_abs_value) * -hparams.min_level_db / hparams.max_abs_value) + hparams.min_level_db)
