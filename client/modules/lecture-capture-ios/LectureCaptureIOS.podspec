Pod::Spec.new do |s|
  s.name         = "LectureCaptureIOS"
  s.version      = "0.0.1"
  s.summary      = "Chunked background lecture recorder for the Lecture Capture app"
  s.homepage     = "https://github.com/dharunsundar-test/lecture-prism"
  s.license      = { :type => "UNLICENSED" }
  s.authors      = "Lecture Capture"
  s.platforms    = { :ios => min_ios_version_supported }
  s.source       = { :path => "." }
  s.source_files = "ios/**/*.{h,m,mm,swift}"
  s.swift_version = "5.0"

  # Provided by React Native's Podfile helpers: adds React-Core and new-architecture settings.
  install_modules_dependencies(s)
end
