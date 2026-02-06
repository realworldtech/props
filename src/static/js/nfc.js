/**
 * Asset Handler NFC Module
 *
 * Provides NFC reading and writing capabilities for the Web NFC API (Android Chrome)
 * For iOS, NFC tags should be programmed with NDEF URL records pointing to asset URLs
 */

const AssetNFC = {
    isSupported: false,
    reader: null,
    abortController: null,

    /**
     * Check if Web NFC API is supported
     */
    checkSupport() {
        this.isSupported = 'NDEFReader' in window;
        return this.isSupported;
    },

    /**
     * Start scanning for NFC tags
     * @param {Function} onRead - Callback when tag is read (receives {serialNumber, records})
     * @param {Function} onError - Callback for errors
     */
    async startReading(onRead, onError) {
        if (!this.checkSupport()) {
            if (onError) onError(new Error('Web NFC not supported'));
            return false;
        }

        try {
            this.reader = new NDEFReader();
            this.abortController = new AbortController();

            await this.reader.scan({ signal: this.abortController.signal });

            this.reader.addEventListener('reading', ({ message, serialNumber }) => {
                const records = [];

                for (const record of message.records) {
                    const decoder = new TextDecoder();
                    let data = null;

                    try {
                        if (record.recordType === 'text') {
                            data = decoder.decode(record.data);
                        } else if (record.recordType === 'url') {
                            data = decoder.decode(record.data);
                        } else if (record.recordType === 'mime') {
                            data = decoder.decode(record.data);
                        }
                    } catch (e) {
                        console.warn('Error decoding record:', e);
                    }

                    records.push({
                        recordType: record.recordType,
                        mediaType: record.mediaType,
                        data: data
                    });
                }

                if (onRead) {
                    onRead({
                        serialNumber: serialNumber,
                        records: records
                    });
                }
            });

            this.reader.addEventListener('readingerror', () => {
                if (onError) onError(new Error('Error reading NFC tag'));
            });

            return true;
        } catch (error) {
            if (onError) onError(error);
            return false;
        }
    },

    /**
     * Stop NFC scanning
     */
    stopReading() {
        if (this.abortController) {
            this.abortController.abort();
            this.abortController = null;
        }
        this.reader = null;
    },

    /**
     * Write a URL record to an NFC tag
     * @param {string} url - The URL to write
     * @param {Function} onSuccess - Callback on success
     * @param {Function} onError - Callback for errors
     */
    async writeUrl(url, onSuccess, onError) {
        if (!this.checkSupport()) {
            if (onError) onError(new Error('Web NFC not supported'));
            return false;
        }

        try {
            const writer = new NDEFReader();

            await writer.write({
                records: [{ recordType: 'url', data: url }]
            });

            if (onSuccess) onSuccess();
            return true;
        } catch (error) {
            if (onError) onError(error);
            return false;
        }
    },

    /**
     * Write an asset tag (URL + ID record) to an NFC tag
     * @param {string} baseUrl - Base URL of the application
     * @param {string} barcode - Asset barcode
     * @param {Function} onSuccess - Callback on success
     * @param {Function} onError - Callback for errors
     */
    async writeAssetTag(baseUrl, barcode, onSuccess, onError) {
        if (!this.checkSupport()) {
            if (onError) onError(new Error('Web NFC not supported'));
            return false;
        }

        const url = `${baseUrl}/a/${barcode}/`;

        try {
            const writer = new NDEFReader();

            await writer.write({
                records: [
                    { recordType: 'url', data: url },
                    { recordType: 'text', data: barcode }
                ]
            });

            if (onSuccess) onSuccess();
            return true;
        } catch (error) {
            if (onError) onError(error);
            return false;
        }
    },

    /**
     * Extract barcode from NFC read result
     * @param {Object} readResult - Result from startReading callback
     * @returns {string|null} Barcode if found
     */
    extractBarcode(readResult) {
        // Check text records first - look for PREFIX-XXXXXXXX pattern
        for (const record of readResult.records) {
            if (record.recordType === 'text' && record.data) {
                // Match any barcode pattern: WORD-HEXCHARS (e.g., ASSET-A1B2C3D4)
                if (/^[A-Z]+-[A-Z0-9]+$/i.test(record.data)) {
                    return record.data;
                }
            }
        }

        // Check URL records for barcode in /a/{barcode}/ path
        for (const record of readResult.records) {
            if (record.recordType === 'url' && record.data) {
                const match = record.data.match(/\/a\/([^\/]+)/);
                if (match) {
                    return match[1];
                }
            }
        }

        return null;
    }
};

// Export for module systems
if (typeof module !== 'undefined' && module.exports) {
    module.exports = AssetNFC;
}
