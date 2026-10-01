#ifndef UART_DRIVER_H
#define UART_DRIVER_H

#include <stddef.h>
#include <stdint.h>

#include "pm_status.h"

/* UART_Driver: the only module that touches Serial (Req 8.3).
 *
 * The header has no Arduino includes, so its interface is plain C types and
 * it could be included from C code too; only uart_driver.cpp sees Serial. */

/* The functions are compiled as C++ in uart_driver.cpp. extern "C" gives them
 * unmangled C names, the same linkage as every other project function, so
 * the header means the same thing whichever language includes it. */
#ifdef __cplusplus
extern "C" {
#endif

/* Starts the hardware UART at UART_BAUD_BPS, 8N1 (Req 8.1). Always PM_OK. */
pm_status_t uart_init(void);

/* Queues `len` bytes of `frame` for transmission without blocking.
 * PM_ERR_UART_BUSY: the TX buffer lacks room for all `len` bytes; nothing written.
 * PM_ERR_UART_WRITE: Serial.write() accepted fewer than `len` bytes.
 * PM_OK: all `len` bytes are queued; the TX interrupt sends them. */
pm_status_t uart_write_frame(const uint8_t *frame, size_t len);

#ifdef __cplusplus
}
#endif

#endif /* UART_DRIVER_H */
