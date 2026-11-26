package keel.api

import keel.api.common.KeelProperties
import keel.api.mcp.McpTokens
import org.assertj.core.api.Assertions.assertThat
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.io.TempDir
import java.nio.file.Files
import java.nio.file.Path

/** 0.41.2 keel-start hands the internal token over as a file only (KEEL_INTERNAL_TOKEN_FILE): a variable would show in
 *  the environment of keel's processes to every program of the same user (`ps eww`). */
class InternalTokenFileTest {

    @Test
    fun `the token comes from the file when there is no value`(@TempDir dir: Path) {
        val f = dir.resolve("internal-token")
        Files.writeString(f, "from-the-file\n")
        val props = KeelProperties(internalTokenFile = f.toString())
        assertThat(props.token).isEqualTo("from-the-file")
        assertThat(McpTokens(props).tokenFor("write")).isEqualTo(McpTokens(KeelProperties(internalToken = "from-the-file")).tokenFor("write"))
    }

    @Test
    fun `a value wins over the file, and a missing file gives no token`(@TempDir dir: Path) {
        val f = dir.resolve("internal-token")
        Files.writeString(f, "from-the-file")
        assertThat(KeelProperties(internalToken = "the-value", internalTokenFile = f.toString()).token).isEqualTo("the-value")
        assertThat(KeelProperties(internalTokenFile = dir.resolve("missing").toString()).token).isEmpty()
        assertThat(KeelProperties().token).isEmpty()
    }
}
