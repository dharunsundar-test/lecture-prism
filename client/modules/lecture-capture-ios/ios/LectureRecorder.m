#import <React/RCTBridgeModule.h>
#import <React/RCTEventEmitter.h>

// Exposes the Swift LectureRecorder class to React Native.
@interface RCT_EXTERN_MODULE(LectureRecorder, RCTEventEmitter)

RCT_EXTERN_METHOD(start:(NSString *)sessionId
                  sessionDir:(NSString *)sessionDir
                  chunkMs:(double)chunkMs
                  startSeq:(double)startSeq
                  resolver:(RCTPromiseResolveBlock)resolve
                  rejecter:(RCTPromiseRejectBlock)reject)

RCT_EXTERN_METHOD(stop:(RCTPromiseResolveBlock)resolve
                  rejecter:(RCTPromiseRejectBlock)reject)

RCT_EXTERN_METHOD(getStatus:(RCTPromiseResolveBlock)resolve
                  rejecter:(RCTPromiseRejectBlock)reject)

@end
