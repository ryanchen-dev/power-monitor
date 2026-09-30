#ifndef PM_STATUS_H
#define PM_STATUS_H

/* The drivers and main are C++ (.cpp); the logic is C. Without extern "C",
 * the C++ compiler would mangle these names and fail to link against the C objects. */
#ifdef __cplusplus
extern "C" {
#endif

/* PM_OK is 0 so a caller can write `if (status != PM_OK)` or treat any
 * non-zero value as an error, the same convention as C library return codes. */
typedef enum {
    PM_OK = 0,
    PM_ERR_I2C_NACK,         /* address or data not acknowledged: sensor missing or unpowered */
    PM_ERR_I2C_BUS,          /* lost arbitration, bus error, or oversized write */
    PM_ERR_I2C_TIMEOUT,      /* Wire timeout fired (no progress for I2C_TIMEOUT_US) */
    PM_ERR_I2C_SHORT_READ,   /* fewer bytes returned than requested */
    PM_ERR_SENSOR_VERIFY,    /* configuration read-back did not match what was written */
    PM_ERR_UART_BUSY,        /* TX buffer lacks room for a whole Frame; nothing written */
    PM_ERR_UART_WRITE,       /* Serial.write() accepted fewer bytes than requested */
    PM_ERR_BUFFER_TOO_SMALL  /* caller's buffer is shorter than a Frame */
} pm_status_t;

#ifdef __cplusplus
}
#endif

#endif /* PM_STATUS_H */
