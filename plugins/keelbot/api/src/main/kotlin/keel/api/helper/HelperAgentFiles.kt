package keel.api.helper

import keel.api.agents.AgentFiles
import keel.api.pluginhost.PluginHost
import org.springframework.stereotype.Component
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.Paths

/**
 * KeelBot's agent (`helper`: its content/agents/helper.md) on keel's Agents page, where keel 0.15.1 listed it with keel's
 * own: a person sets its model, knowledge and skills there, and every KeelBot turn uses them ([HelperService]). The file
 * is in KeelBot's plugin folder, which the plugin host knows (run/resolved.json).
 */
@Component
class HelperAgentFiles(private val host: PluginHost) : AgentFiles {
    override fun files(): List<Path> {
        val content = host.plugins.firstOrNull { it.name == PLUGIN }?.content ?: return emptyList()
        val dir = Paths.get(content, "agents")
        if (!Files.isDirectory(dir)) return emptyList()
        return Files.list(dir).use { s -> s.filter { it.toString().endsWith(".md") }.sorted().toList() }
    }

    companion object {
        /** This plugin's name (keel-plugin.yml). */
        const val PLUGIN = "keelbot"
    }
}
