const path = require('path');

module.exports = {
  dependencies: {
    // Local iOS recorder module, linked by `pod install` so the Xcode project needn't be edited.
    // Android's recorder lives in the app module (android/app/.../recorder) instead.
    'lecture-capture-ios': {
      root: path.join(__dirname, 'modules/lecture-capture-ios'),
      platforms: {
        android: null,
      },
    },
  },
};
