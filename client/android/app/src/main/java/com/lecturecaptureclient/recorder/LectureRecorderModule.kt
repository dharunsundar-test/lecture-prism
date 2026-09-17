package com.lecturecaptureclient.recorder

import android.content.Intent
import androidx.core.content.ContextCompat
import com.facebook.react.bridge.Arguments
import com.facebook.react.bridge.Promise
import com.facebook.react.bridge.ReactApplicationContext
import com.facebook.react.bridge.ReactContextBaseJavaModule
import com.facebook.react.bridge.ReactMethod
import com.facebook.react.bridge.WritableMap
import com.facebook.react.modules.core.DeviceEventManagerModule
import java.io.File

/**
 * JS interface to [RecorderService].
 *
 * Events: "LectureRecorder.chunk" (one per finished chunk) and "LectureRecorder.error".
 * stop() resolves with the next chunk sequence number, which resume passes back to start().
 */
class LectureRecorderModule(private val reactContext: ReactApplicationContext) :
  ReactContextBaseJavaModule(reactContext), RecorderService.Listener {

  companion object {
    const val NAME = "LectureRecorder"
    const val EVENT_CHUNK = "LectureRecorder.chunk"
    const val EVENT_ERROR = "LectureRecorder.error"
  }

  private var active = false
  private var nextSeq = 0
  private var pendingStop: Promise? = null

  init {
    RecorderService.listener = this
  }

  override fun getName() = NAME

  @ReactMethod
  fun start(sessionId: String, sessionDir: String, chunkMs: Double, startSeq: Double, promise: Promise) {
    if (active) {
      promise.reject("E_ALREADY_RECORDING", "A recording is already in progress")
      return
    }
    File(sessionDir).mkdirs()
    val intent = Intent(reactContext, RecorderService::class.java).apply {
      action = RecorderService.ACTION_START
      putExtra(RecorderService.EXTRA_SESSION_ID, sessionId)
      putExtra(RecorderService.EXTRA_SESSION_DIR, sessionDir)
      putExtra(RecorderService.EXTRA_CHUNK_MS, chunkMs.toLong())
      putExtra(RecorderService.EXTRA_START_SEQ, startSeq.toInt())
    }
    try {
      ContextCompat.startForegroundService(reactContext, intent)
      active = true
      nextSeq = startSeq.toInt()
      promise.resolve(null)
    } catch (e: Exception) {
      promise.reject("E_START_FAILED", e.message ?: "Could not start the recording service", e)
    }
  }

  @ReactMethod
  fun stop(promise: Promise) {
    if (!active) {
      promise.resolve(nextSeq)
      return
    }
    pendingStop?.resolve(nextSeq)
    pendingStop = promise
    reactContext.startService(Intent(reactContext, RecorderService::class.java).setAction(RecorderService.ACTION_STOP))
  }

  @ReactMethod
  fun getStatus(promise: Promise) {
    promise.resolve(Arguments.createMap().apply {
      putBoolean("recording", active)
      putInt("nextSeq", nextSeq)
    })
  }

  // Required by NativeEventEmitter; events are delivered through DeviceEventEmitter.
  @ReactMethod
  fun addListener(eventName: String) {}

  @ReactMethod
  fun removeListeners(count: Double) {}

  override fun onChunkFinished(chunk: RecorderService.ChunkInfo) {
    nextSeq = chunk.seq + 1
    emit(EVENT_CHUNK, Arguments.createMap().apply {
      putString("sessionId", chunk.sessionId)
      putInt("seq", chunk.seq)
      putString("path", chunk.path)
      putDouble("startedAt", chunk.startedAt.toDouble())
      putDouble("endedAt", chunk.endedAt.toDouble())
      putDouble("sizeBytes", chunk.sizeBytes.toDouble())
    })
  }

  override fun onRecordingError(message: String) {
    active = false
    emit(EVENT_ERROR, Arguments.createMap().apply { putString("message", message) })
    resolvePendingStop()
  }

  override fun onRecordingStopped() {
    active = false
    resolvePendingStop()
  }

  private fun resolvePendingStop() {
    pendingStop?.resolve(nextSeq)
    pendingStop = null
  }

  private fun emit(event: String, params: WritableMap) {
    if (!reactContext.hasActiveReactInstance()) return
    reactContext
      .getJSModule(DeviceEventManagerModule.RCTDeviceEventEmitter::class.java)
      .emit(event, params)
  }
}
