/* UART_Driver: the only file that uses Serial (Req 8.3).
 *
 * Only Frames go on the wire. There is no Serial.print or println anywhere in
 * the firmware: the host parses a binary stream, and any text mixed in would
 * show up as garbage bytes and false sync candidates (Req 8.3). Never calling
 * print also keeps Print's number-formatting code (about 1 KB) out of flash,
 * because the linker drops functions nothing calls.
 *
 * Baud rate note: at 16 MHz the AVR core sets up 115200 baud in double-speed
 * mode with UBRR = 16, which gives 16,000,000 / (8 x 17) = 117,647 baud, 2.1%
 * fast. The Uno's ATmega16U2 USB bridge derives its UART clock with the same
 * formula from the same 16 MHz frequency, so both ends run at 117,647 baud
 * and the offset cancels. The Mac's side is USB CDC, which has no baud rate
 * on the wire at all. The 10-minute link test (Req 17.6) confirms this. */

#include "uart_driver.h"

#include <Arduino.h>

#include "pm_config.h"   /* UART_BAUD_BPS */

pm_status_t uart_init(void)
{
    Serial.begin(UART_BAUD_BPS, SERIAL_8N1);   /* Req 8.1 */
    /* Serial.begin has no failure path on the AVR (it only sets registers),
     * but every function that touches hardware returns a status, so callers
     * treat all drivers the same way and a future driver that can fail
     * doesn't change the calling code. */
    return PM_OK;
}

pm_status_t uart_write_frame(const uint8_t *frame, size_t len)
{
    /* Non-blocking by design. Serial.write() copies bytes into a 64-byte TX
     * ring buffer that the TX interrupt drains; when the buffer is full,
     * HardwareSerial::write spins until the interrupt frees a slot. Blocking
     * here would delay loop() and push back the next Sample. So the whole
     * Frame is written only if it fits now, and skipped otherwise.
     *
     * 63 of the 64 bytes are usable: a ring buffer keeps one slot empty so
     * that head == tail means "empty" and can't also mean "full". A 25-byte
     * Frame always fits once the previous one has drained (25 bytes x 10 bits
     * / 115,200 baud = 2.17 ms, far less than a Sample_Period).
     *
     * This pre-check is what makes the write non-blocking: this function is
     * the only code that adds to the TX buffer and the interrupt only removes
     * from it, so the room can only grow between the check and the write, and
     * write() never reaches its wait loop. */
    int room = Serial.availableForWrite();

    /* availableForWrite() returns int and len is size_t (unsigned, 16 bits
     * on the AVR). Comparing them directly would convert room to unsigned
     * and warn (-Wsign-compare); casting len to int instead could turn a
     * len above INT_MAX into a negative number. So a negative room (not
     * possible on this core, but the type allows it) is rejected first, and
     * only then is the non-negative room converted to size_t, which is exact. */
    if (room < 0 || (size_t)room < len) {
        return PM_ERR_UART_BUSY;
    }

    size_t written = Serial.write(frame, len);

    /* Required by the design, but unreachable on Arduino AVR core 1.8.8:
     * HardwareSerial::write(uint8_t) always returns 1 (it waits for room
     * rather than failing), and Print::write(buffer, size) stops early only
     * when a single-byte write returns 0, so it always returns len. The check
     * stays so a different core or Serial implementation can't lose bytes
     * silently. */
    if (written < len) {
        return PM_ERR_UART_WRITE;
    }
    return PM_OK;
}
