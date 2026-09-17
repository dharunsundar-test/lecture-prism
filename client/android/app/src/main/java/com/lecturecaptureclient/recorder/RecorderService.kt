package com.lecturecaptureclient.recorder

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.media.MediaRecorder
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.PowerManager
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import org.json.JSONObject
import java.io.File

/**
 * Records the lecture in fixed-length chunks from a microphone foreground service, so recording
 * keeps going with the screen off or the app in the background. Chunk rotation runs here rather
 * than in JS because JS timers are not reliable once the app is backgrounded.
 *
 * Each finished chunk gets a JSON sidecar next to the audio file, so the app can recover chunks
 * that finished while the JS side wasn't running.
 */
class RecorderService : Service() {

  data class ChunkInfo(
    val sessionId: String,
    val seq: Int,
    val path: String,
    val startedAt: Long,
    val endedAt: Long,
    val sizeBytes: Long,
  )

  interface Listener {
    fun onChunkFinished(chunk: ChunkInfo)
    fun onRecordingError(message: String)
    fun onRecordingStopped()
  }

  companion object {
    const val ACTION_START = "com.lecturecaptureclient.recorder.START"
    const val ACTION_STOP = "com.lecturecaptureclient.recorder.STOP"
    const val EXTRA_SESSION_ID = "sessionId"
    const val EXTRA_SESSION_DIR = "sessionDir"
    const val EXTRA_CHUNK_MS = "chunkMs"
    const val EXTRA_START_SEQ = "startSeq"

    private const val NOTIFICATION_ID = 4101
    private const val CHANNEL_ID = "lecture_recording"
    private const val WAKE_LOCK_TIMEOUT_MS = 4 * 60 * 60 * 1000L

    @Volatile var listener: Listener? = null
  }

  private val handler = Handler(Looper.getMainLooper())
  private val rotateRunnable = Runnable { rotateChunk() }

  private var recorder: MediaRecorder? = null
  private var wakeLock: PowerManager.WakeLock? = null
  private var sessionId = ""
  private var sessionDir = ""
  private var chunkMs = 5 * 60 * 1000L
  private var seq = 0
  private var chunkStartedAt = 0L
  private var chunkPath = ""

  override fun onBind(intent: Intent?): IBinder? = null

  override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
    when (intent?.action) {
      ACTION_START -> handleStart(intent)
      ACTION_STOP -> handleStop()
      else -> stopSelf()
    }
    return START_NOT_STICKY
  }

  override fun onDestroy() {
    finishChunk()
    releaseWakeLock()
    super.onDestroy()
  }

  private fun handleStart(intent: Intent) {
    // Must be called promptly after startForegroundService, even if we bail out below.
    startInForeground()
    if (recorder != null) return

    val id = intent.getStringExtra(EXTRA_SESSION_ID)
    val dir = intent.getStringExtra(EXTRA_SESSION_DIR)
    if (id.isNullOrEmpty() || dir.isNullOrEmpty()) {
      fail("Recording was started without a session")
      return
    }
    sessionId = id
    sessionDir = dir
    chunkMs = intent.getLongExtra(EXTRA_CHUNK_MS, chunkMs)
    seq = intent.getIntExtra(EXTRA_START_SEQ, 0)
    File(sessionDir).mkdirs()

    acquireWakeLock()
    try {
      startChunk()
    } catch (e: Exception) {
      fail("Could not start the microphone: ${e.message}")
    }
  }

  private fun handleStop() {
    finishChunk()
    shutdown()
    listener?.onRecordingStopped()
  }

  private fun startChunk() {
    val path = File(sessionDir, "chunk_$seq.m4a").absolutePath
    val r = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) MediaRecorder(this) else @Suppress("DEPRECATION") MediaRecorder()
    try {
      r.setAudioSource(MediaRecorder.AudioSource.MIC)
      r.setOutputFormat(MediaRecorder.OutputFormat.MPEG_4)
      r.setAudioEncoder(MediaRecorder.AudioEncoder.AAC)
      r.setAudioChannels(1)
      r.setAudioSamplingRate(44100)
      r.setAudioEncodingBitRate(64000)
      r.setOutputFile(path)
      r.prepare()
      r.start()
    } catch (e: Exception) {
      r.release()
      throw e
    }
    recorder = r
    chunkPath = path
    chunkStartedAt = System.currentTimeMillis()
    handler.postDelayed(rotateRunnable, chunkMs)
  }

  private fun rotateChunk() {
    finishChunk()
    try {
      startChunk()
    } catch (e: Exception) {
      fail("Could not start the next chunk: ${e.message}")
    }
  }

  /** Stops the current chunk and reports it. Safe to call when nothing is recording. */
  private fun finishChunk() {
    val r = recorder ?: return
    recorder = null
    handler.removeCallbacks(rotateRunnable)

    val endedAt = System.currentTimeMillis()
    // stop() throws if no audio was captured (e.g. stopped immediately after starting).
    val stopped = try {
      r.stop()
      true
    } catch (e: RuntimeException) {
      false
    }
    r.release()

    val file = File(chunkPath)
    if (!stopped || !file.exists() || file.length() == 0L) {
      file.delete()
      return
    }

    val chunk = ChunkInfo(sessionId, seq, chunkPath, chunkStartedAt, endedAt, file.length())
    writeSidecar(chunk)
    seq += 1
    listener?.onChunkFinished(chunk)
  }

  private fun writeSidecar(chunk: ChunkInfo) {
    val json = JSONObject()
      .put("sessionId", chunk.sessionId)
      .put("seq", chunk.seq)
      .put("path", chunk.path)
      .put("startedAt", chunk.startedAt)
      .put("endedAt", chunk.endedAt)
      .put("sizeBytes", chunk.sizeBytes)
    File(sessionDir, "chunk_${chunk.seq}.json").writeText(json.toString())
  }

  private fun fail(message: String) {
    finishChunk()
    shutdown()
    listener?.onRecordingError(message)
  }

  private fun shutdown() {
    releaseWakeLock()
    ServiceCompat.stopForeground(this, ServiceCompat.STOP_FOREGROUND_REMOVE)
    stopSelf()
  }

  private fun startInForeground() {
    val manager = getSystemService(NotificationManager::class.java)
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
      manager.createNotificationChannel(
        NotificationChannel(CHANNEL_ID, "Lecture recording", NotificationManager.IMPORTANCE_LOW)
      )
    }
    val type = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE else 0
    ServiceCompat.startForeground(this, NOTIFICATION_ID, buildNotification(), type)
  }

  private fun buildNotification(): Notification {
    val launchIntent = packageManager.getLaunchIntentForPackage(packageName)
    val contentIntent = launchIntent?.let {
      PendingIntent.getActivity(this, 0, it, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
    }
    return NotificationCompat.Builder(this, CHANNEL_ID)
      .setContentTitle("Recording lecture")
      .setContentText("Audio is saved on this phone and sent to your PC when it's reachable")
      .setSmallIcon(android.R.drawable.ic_btn_speak_now)
      .setOngoing(true)
      .setContentIntent(contentIntent)
      .build()
  }

  private fun acquireWakeLock() {
    if (wakeLock?.isHeld == true) return
    val power = getSystemService(PowerManager::class.java)
    wakeLock = power.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "LectureCapture:recording").apply {
      setReferenceCounted(false)
      acquire(WAKE_LOCK_TIMEOUT_MS)
    }
  }

  private fun releaseWakeLock() {
    wakeLock?.let { if (it.isHeld) it.release() }
    wakeLock = null
  }
}
