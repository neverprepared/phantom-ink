class Reflex < Formula
  desc "Claude Code plugin for development workflows, skills, and MCP management"
  homepage "https://github.com/neverprepared/ink-bunny"
  url "https://github.com/neverprepared/ink-bunny/releases/download/reflex-v1.26.0/reflex-1.26.0.tar.gz"
  version "1.26.0"
  sha256 "e0ac91ba7187c8dfd508ded305c7696a99ece3d7cf81051211586423ed197fa6"
  license "MIT"

  def install
    (share/"reflex").install Dir["plugins/reflex/*"]
  end

  def caveats
    <<~EOS
      To use reflex with Claude Code:

        claude --plugin-dir #{share}/reflex

      Or install from the plugin marketplace:

        /plugin marketplace add mindmorass/reflex
    EOS
  end

  test do
    assert_path_exists share/"reflex/.claude-plugin/plugin.json"
  end
end
