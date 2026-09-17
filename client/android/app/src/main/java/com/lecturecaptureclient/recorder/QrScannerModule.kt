package com.lecturecaptureclient.recorder

import com.facebook.react.bridge.Promise
import com.facebook.react.bridge.ReactApplicationContext
import com.facebook.react.bridge.ReactContextBaseJavaModule
import com.facebook.react.bridge.ReactMethod
import com.google.mlkit.vision.barcode.common.Barcode
import com.google.mlkit.vision.codescanner.GmsBarcodeScannerOptions
import com.google.mlkit.vision.codescanner.GmsBarcodeScanning

/**
 * Scans the pairing QR code with Google's code scanner. It supplies its own camera UI and
 * doesn't need the CAMERA permission. Requires Google Play services.
 */
class QrScannerModule(private val reactContext: ReactApplicationContext) :
  ReactContextBaseJavaModule(reactContext) {

  override fun getName() = "QrScanner"

  /** Resolves with the QR text, or null if the user cancelled. */
  @ReactMethod
  fun scan(promise: Promise) {
    val activity = reactContext.currentActivity
    if (activity == null) {
      promise.reject("E_NO_ACTIVITY", "The app must be in the foreground to scan")
      return
    }
    val options = GmsBarcodeScannerOptions.Builder()
      .setBarcodeFormats(Barcode.FORMAT_QR_CODE)
      .build()
    GmsBarcodeScanning.getClient(activity, options)
      .startScan()
      .addOnSuccessListener { barcode -> promise.resolve(barcode.rawValue) }
      .addOnCanceledListener { promise.resolve(null) }
      .addOnFailureListener { e -> promise.reject("E_SCAN_FAILED", e.message ?: "QR scan failed", e) }
  }
}
