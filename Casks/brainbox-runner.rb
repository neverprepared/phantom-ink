cask "brainbox-runner" do
  version "0.1.9"
  sha256 "94d73e9fe6502c6c23e811a486d47acc37c11652c8ae14fd959251a077ac5612"

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
