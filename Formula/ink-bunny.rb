class InkBunny < Formula
  desc "Agentic development platform (brainbox + shell-profiler)"
  homepage "https://github.com/neverprepared/phantom-ink"
  url "https://github.com/neverprepared/phantom-ink/releases/download/brainbox/v0.17.0/brainbox-0.17.0.tar.gz"
  version "0.17.0"
  sha256 "placeholder"
  license "MIT"

  depends_on "neverprepared/ink-bunny/brainbox"
  depends_on "neverprepared/ink-bunny/shell-profiler"

  def install
    # Meta-formula — all work done by dependencies
  end

  test do
    assert_match "brainbox", shell_output("#{bin}/brainbox version")
  end
end
