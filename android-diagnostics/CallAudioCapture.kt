package com.example.phonenotify.diagnostics

import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.util.Log
import java.io.File
import java.io.FileOutputStream
import java.io.RandomAccessFile

/**
 * Prueba de diagnostico: intenta capturar el audio real de una llamada
 * en curso via AudioSource.VOICE_DOWNLINK (con fallback a VOICE_CALL),
 * ahora que la app tiene CAPTURE_AUDIO_OUTPUT concedido (v0.8.0, priv-app).
 *
 * Objetivo de esta prueba: confirmar si el chipset/vendor HAL de este
 * telefono entrega audio real por esta via, o solo silencio/ceros (cosa
 * que pasa en algunos chipsets pese a tener el permiso concedido).
 */
class CallAudioCapture(private val outFile: File) {

    companion object {
        private const val TAG = "alarma-dialer-capture"
        private const val SAMPLE_RATE = 8000 // audio de llamada GSM/CS suele ser narrowband 8kHz
    }

    @Volatile private var recording = false
    private var thread: Thread? = null

    fun start(durationSeconds: Int) {
        if (recording) return
        recording = true
        thread = Thread {
            captureLoop(durationSeconds)
        }.also { it.start() }
    }

    fun stop() {
        recording = false
        thread?.join(2000)
    }

    private fun tryCreateRecorder(source: Int): AudioRecord? {
        val minBuf = AudioRecord.getMinBufferSize(
            SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT
        )
        if (minBuf <= 0) {
            Log.e(TAG, "getMinBufferSize invalido: $minBuf")
            return null
        }
        return try {
            val rec = AudioRecord(
                source, SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT, minBuf * 4
            )
            if (rec.state != AudioRecord.STATE_INITIALIZED) {
                Log.e(TAG, "AudioRecord source=$source no se inicializo (state=${rec.state})")
                rec.release()
                null
            } else {
                Log.i(TAG, "AudioRecord source=$source inicializado OK")
                rec
            }
        } catch (e: Exception) {
            Log.e(TAG, "AudioRecord source=$source lanzo excepcion: ${e.message}", e)
            null
        }
    }

    private fun captureLoop(durationSeconds: Int) {
        // VOICE_DOWNLINK primero (solo lo que dice el receptor), fallback a VOICE_CALL (ambas vias mezcladas)
        var source = MediaRecorder.AudioSource.VOICE_DOWNLINK
        var recorder = tryCreateRecorder(source)
        if (recorder == null) {
            source = MediaRecorder.AudioSource.VOICE_CALL
            recorder = tryCreateRecorder(source)
        }
        if (recorder == null) {
            Log.e(TAG, "Ninguna fuente de audio de llamada disponible, abortando captura")
            recording = false
            return
        }

        Log.i(TAG, "Capturando con source=$source durante ${durationSeconds}s -> ${outFile.absolutePath}")
        val minBuf = AudioRecord.getMinBufferSize(
            SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT
        )
        val buffer = ShortArray(minBuf / 2)
        var totalBytes = 0L
        var maxAbsSample = 0

        FileOutputStream(outFile).use { fos ->
            writeWavHeaderPlaceholder(fos)
            recorder.startRecording()
            val startMs = System.currentTimeMillis()
            while (recording && System.currentTimeMillis() - startMs < durationSeconds * 1000L) {
                val n = recorder.read(buffer, 0, buffer.size)
                if (n > 0) {
                    for (i in 0 until n) {
                        val abs = kotlin.math.abs(buffer[i].toInt())
                        if (abs > maxAbsSample) maxAbsSample = abs
                    }
                    val bytes = ByteArray(n * 2)
                    for (i in 0 until n) {
                        val v = buffer[i].toInt()
                        bytes[i * 2] = (v and 0xFF).toByte()
                        bytes[i * 2 + 1] = ((v shr 8) and 0xFF).toByte()
                    }
                    fos.write(bytes)
                    totalBytes += bytes.size
                }
            }
            recorder.stop()
            recorder.release()
        }
        patchWavHeader(outFile, totalBytes)
        recording = false
        Log.i(TAG, "Captura terminada: $totalBytes bytes, maxAbsSample=$maxAbsSample " +
                "(si es 0 o muy bajo -> silencio/no hay audio real; si es alto -> audio real capturado)")
    }

    private fun writeWavHeaderPlaceholder(fos: FileOutputStream) {
        // 44 bytes de cabecera WAV, rellenados con datos reales al final via patchWavHeader
        fos.write(ByteArray(44))
    }

    private fun patchWavHeader(file: File, dataBytes: Long) {
        val byteRate = SAMPLE_RATE * 2
        val header = ByteArray(44)
        fun putStr(off: Int, s: String) { s.forEachIndexed { i, c -> header[off + i] = c.code.toByte() } }
        fun putInt(off: Int, v: Int) {
            header[off] = (v and 0xFF).toByte()
            header[off + 1] = ((v shr 8) and 0xFF).toByte()
            header[off + 2] = ((v shr 16) and 0xFF).toByte()
            header[off + 3] = ((v shr 24) and 0xFF).toByte()
        }
        fun putShort(off: Int, v: Int) {
            header[off] = (v and 0xFF).toByte()
            header[off + 1] = ((v shr 8) and 0xFF).toByte()
        }
        putStr(0, "RIFF")
        putInt(4, (36 + dataBytes).toInt())
        putStr(8, "WAVE")
        putStr(12, "fmt ")
        putInt(16, 16)
        putShort(20, 1) // PCM
        putShort(22, 1) // mono
        putInt(24, SAMPLE_RATE)
        putInt(28, byteRate)
        putShort(32, 2) // block align
        putShort(34, 16) // bits per sample
        putStr(36, "data")
        putInt(40, dataBytes.toInt())
        RandomAccessFile(file, "rw").use { raf ->
            raf.seek(0)
            raf.write(header)
        }
    }
}
