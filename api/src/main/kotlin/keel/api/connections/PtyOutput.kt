package keel.api.connections

/** How the GitHub sign-in reads gh's terminal output: the reader loop and the ANSI strip. */
internal object PtyOutput {
    private val csi = Regex("\u001B\\[[0-9;?]*[ -/]*[@-~]")
    private val osc = Regex("\u001B][^\u0007]*\u0007")

    /** The buffer's text without terminal escape codes. */
    fun clean(out: StringBuilder): String = synchronized(out) { out.toString().replace(csi, "").replace(osc, "") }

    /** Reads [process] output into [out] until it ends; past [cap] chars only the last [keep] stay. [onChunk] runs after each read. */
    fun pump(process: Process, out: StringBuilder, cap: Int, keep: Int, onChunk: () -> Unit) {
        val buf = ByteArray(4096)
        val input = process.inputStream
        while (true) {
            val n = try { input.read(buf) } catch (e: Exception) { -1 }
            if (n < 0) break
            synchronized(out) {
                out.append(String(buf, 0, n, Charsets.UTF_8))
                if (out.length > cap) out.delete(0, out.length - keep)
            }
            onChunk()
        }
    }
}
