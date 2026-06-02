cask "brainbox-runner" do
  version "0.1.8"
  sha256 "13ad357830e8ad90c2c674f78653e6eb4fab7f33a0dbdbe1042f18c4db2896cc"

  url "https://github.com/neverprepared/phantom-ink/releases/download/runner/v#{version}/BrainboxRunner.dmg"
  name "Brainbox Runner"
  desc "Menu-bar runner that connects to a brainbox API and executes session work"
  homepage "https://github.com/neverprepared/phantom-ink"

  depends_on macos: ">= :ventura"

  app "BrainboxRunner.app"

  zap trash: [
    "~/Library/Preferences/com.neverprepared.brainbox-runner.plist",
    "~/Library/Application Scripts/com.neverprepared.brainbox-runner",
    "~/Library/Saved Application State/com.neverprepared.brainbox-runner.savedState",
  ]
end
