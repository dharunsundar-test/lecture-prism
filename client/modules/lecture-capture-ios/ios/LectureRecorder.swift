import AVFoundation
import Foundation
import React

/// iOS counterpart of the Android RecorderService/LectureRecorderModule, with the same JS API.
///
/// Records the lecture in fixed-length AAC chunks with AVAudioRecorder. With the `audio`
/// background mode, the app keeps running while recording, so the rotation timer keeps firing
/// with the screen locked. Each finished chunk gets a JSON sidecar next to the audio file.
///
/// Events: "LectureRecorder.chunk" (one per finished chunk) and "LectureRecorder.error".
@objc(LectureRecorder)
class LectureRecorder: RCTEventEmitter {
  private static let chunkEvent = "LectureRecorder.chunk"
  private static let errorEvent = "LectureRecorder.error"

  private var recorder: AVAudioRecorder?
  private var rotateTimer: Timer?
  private var active = false
  private var hasListeners = false
  private var sessionId = ""
  private var sessionDir = ""
  private var chunkMs: Double = 5 * 60 * 1000
  private var seq = 0
  private var chunkStartedAt: Double = 0
  private var chunkPath = ""

  override static func requiresMainQueueSetup() -> Bool { true }

  override func supportedEvents() -> [String]! {
    [Self.chunkEvent, Self.errorEvent]
  }

  override func startObserving() { hasListeners = true }
  override func stopObserving() { hasListeners = false }

  deinit {
    NotificationCenter.default.removeObserver(self)
  }

  @objc(start:sessionDir:chunkMs:startSeq:resolver:rejecter:)
  func start(
    _ sessionId: String,
    sessionDir: String,
    chunkMs: Double,
    startSeq: Double,
    resolver resolve: @escaping RCTPromiseResolveBlock,
    rejecter reject: @escaping RCTPromiseRejectBlock
  ) {
    DispatchQueue.main.async {
      if self.active {
        reject("E_ALREADY_RECORDING", "A recording is already in progress", nil)
        return
      }
      AVAudioSession.sharedInstance().requestRecordPermission { granted in
        DispatchQueue.main.async {
          guard granted else {
            reject("E_PERMISSION", "Microphone permission denied", nil)
            return
          }
          do {
            let audioSession = AVAudioSession.sharedInstance()
            try audioSession.setCategory(.record, mode: .default)
            try audioSession.setActive(true)
            try FileManager.default.createDirectory(atPath: sessionDir, withIntermediateDirectories: true)

            self.sessionId = sessionId
            self.sessionDir = sessionDir
            self.chunkMs = chunkMs
            self.seq = Int(startSeq)
            try self.startChunk()

            self.active = true
            NotificationCenter.default.addObserver(
              self,
              selector: #selector(self.handleInterruption(_:)),
              name: AVAudioSession.interruptionNotification,
              object: audioSession
            )
            resolve(nil)
          } catch {
            self.deactivate()
            reject("E_START_FAILED", error.localizedDescription, error)
          }
        }
      }
    }
  }

  /// Resolves with the sequence number the next chunk should use.
  @objc(stop:rejecter:)
  func stop(_ resolve: @escaping RCTPromiseResolveBlock, rejecter reject: @escaping RCTPromiseRejectBlock) {
    DispatchQueue.main.async {
      self.finishChunk()
      self.deactivate()
      resolve(self.seq)
    }
  }

  @objc(getStatus:rejecter:)
  func getStatus(_ resolve: @escaping RCTPromiseResolveBlock, rejecter reject: @escaping RCTPromiseRejectBlock) {
    DispatchQueue.main.async {
      resolve(["recording": self.active, "nextSeq": self.seq])
    }
  }

  private func startChunk() throws {
    let path = (sessionDir as NSString).appendingPathComponent("chunk_\(seq).m4a")
    let settings: [String: Any] = [
      AVFormatIDKey: kAudioFormatMPEG4AAC,
      AVSampleRateKey: 44100,
      AVNumberOfChannelsKey: 1,
      AVEncoderBitRateKey: 64000,
    ]
    let newRecorder = try AVAudioRecorder(url: URL(fileURLWithPath: path), settings: settings)
    guard newRecorder.record() else {
      throw NSError(domain: "LectureRecorder", code: 1, userInfo: [NSLocalizedDescriptionKey: "Could not start the microphone"])
    }
    recorder = newRecorder
    chunkPath = path
    chunkStartedAt = Date().timeIntervalSince1970 * 1000
    rotateTimer = Timer.scheduledTimer(withTimeInterval: chunkMs / 1000, repeats: false) { [weak self] _ in
      self?.rotateChunk()
    }
  }

  private func rotateChunk() {
    finishChunk()
    do {
      try startChunk()
    } catch {
      fail("Could not start the next chunk: \(error.localizedDescription)")
    }
  }

  /// Stops the current chunk and reports it. Safe to call when nothing is recording.
  private func finishChunk() {
    guard let current = recorder else { return }
    recorder = nil
    rotateTimer?.invalidate()
    rotateTimer = nil

    current.stop()
    let endedAt = Date().timeIntervalSince1970 * 1000
    let attributes = try? FileManager.default.attributesOfItem(atPath: chunkPath)
    let size = (attributes?[.size] as? NSNumber)?.int64Value ?? 0
    guard size > 0 else {
      try? FileManager.default.removeItem(atPath: chunkPath)
      return
    }

    let chunk: [String: Any] = [
      "sessionId": sessionId,
      "seq": seq,
      "path": chunkPath,
      "startedAt": chunkStartedAt,
      "endedAt": endedAt,
      "sizeBytes": size,
    ]
    writeSidecar(chunk)
    seq += 1
    if hasListeners {
      sendEvent(withName: Self.chunkEvent, body: chunk)
    }
  }

  private func writeSidecar(_ chunk: [String: Any]) {
    let path = (sessionDir as NSString).appendingPathComponent("chunk_\(chunk["seq"] ?? seq).json")
    if let data = try? JSONSerialization.data(withJSONObject: chunk) {
      FileManager.default.createFile(atPath: path, contents: data)
    }
  }

  /// A phone call or another app taking the microphone stops the recorder. Save what was
  /// recorded and let JS show the session as paused so the user can resume.
  @objc private func handleInterruption(_ notification: Notification) {
    guard
      let rawType = notification.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt,
      AVAudioSession.InterruptionType(rawValue: rawType) == .began,
      active
    else { return }
    fail("Recording was interrupted by another app or a call")
  }

  private func fail(_ message: String) {
    finishChunk()
    deactivate()
    if hasListeners {
      sendEvent(withName: Self.errorEvent, body: ["message": message])
    }
  }

  private func deactivate() {
    active = false
    NotificationCenter.default.removeObserver(self, name: AVAudioSession.interruptionNotification, object: nil)
    try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
  }
}
