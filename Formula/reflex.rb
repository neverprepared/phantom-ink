class Reflex < Formula
  desc "Claude Code plugin for development workflows, skills, and MCP management"
  homepage "https://github.com/neverprepared/phantom-ink"
  url "https://github.com/neverprepared/phantom-ink/releases/download/reflex-v1.27.0/reflex-1.27.0.tar.gz"
  version "1.27.0"
  sha256 "placeholder"
  license "MIT"

  def install
    target = Pathname.new(Dir.home)/"bin"/"reflex"
    target.mkpath
    target.install Dir["plugins/reflex/*"]
  end

  def caveats
    <<~EOS
      Installed to ~/bin/reflex

      To use reflex with Claude Code:

        claude --plugin-dir ~/bin/reflex

      Or install from the plugin marketplace:

        /plugin marketplace add mindmorass/reflex

      NOTE: Because this formula installs outside the Homebrew prefix,
      uninstalling via `brew uninstall reflex` will NOT remove the plugin files.
      To fully remove, also run:

        rm -rf ~/bin/reflex
    EOS
  end

  test do
    assert_path_exists Pathname.new(Dir.home)/"bin"/"reflex/.claude-plugin/plugin.json"
  end
end
