cask "brainbox-runner" do
  version "0.1.10"
  sha256 "93eccb134a50465270b310b5506c7b4afb4010632b4a58e0867b286fb6fe9dd6"

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
