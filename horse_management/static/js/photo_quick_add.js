/* Quick-add photo form (templates/horses/partials/photo_form.html).
 *
 * Registered as an Alpine component so the same form works on its full
 * page and inside the pop-up sheet (where an inline <script> would race
 * Alpine's initialisation of the swapped-in markup). Accumulates files
 * from the camera and gallery inputs into one DataTransfer and mirrors
 * them onto the real, submitted `images` input.
 *
 * A photo is made smaller on the phone before it is sent
 * (docs/MOBILE_APP_PLAN.md, item B8). A phone photo is 3 to 10 MB; the
 * server takes 5 MB at most, and on a weak signal a full-size photo takes
 * minutes. The longest side becomes 2,000 px at most and the file a JPEG,
 * about 500 KB. A photo that cannot be read here (HEIC on some phones), a
 * PNG, and a photo that is small already go as they are; the server
 * checks every file as before.
 */
(function (root) {
    'use strict';

    // ── Pure helpers, unit-tested in tests/photo_quick_add.test.js ──

    var MAX_SIDE = 2000;
    var JPEG_QUALITY = 0.82;
    var SMALL_ENOUGH_BYTES = 700 * 1024;

    /** The size to draw a photo at: the longest side MAX_SIDE at most. */
    function fitWithin(width, height, maxSide) {
        var longest = Math.max(width, height);
        if (!longest || longest <= maxSide) { return { width: width, height: height, scale: 1 }; }
        var scale = maxSide / longest;
        return { width: Math.round(width * scale), height: Math.round(height * scale), scale: scale };
    }

    /**
     * Is this a file to make smaller? Camera photos are JPEG (an iPhone
     * hands a HEIC photo to the page as JPEG). A PNG can have clear parts
     * that a JPEG cannot keep, and is most often a screenshot: leave it.
     */
    function canShrink(type) {
        return type === 'image/jpeg' || type === 'image/webp';
    }

    /** After the change the file is a JPEG, so its name must say so. */
    function jpegName(name) {
        var base = String(name || 'photo').replace(/\.[A-Za-z0-9]{2,5}$/, '');
        return (base || 'photo') + '.jpg';
    }

    /** "480 KB" or "3.2 MB". */
    function sizeLabel(bytes) {
        if (bytes < 1024 * 1024) { return Math.max(1, Math.round(bytes / 1024)) + ' KB'; }
        return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
    }

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = {
            fitWithin: fitWithin, canShrink: canShrink, jpegName: jpegName, sizeLabel: sizeLabel,
            MAX_SIDE: MAX_SIDE,
        };
    }
    if (!root.document) { return; }

    var doc = root.document;

    /** Resolves with a smaller JPEG, or with the file itself when that is not possible or not useful. */
    function shrink(file) {
        return new Promise(function (resolve) {
            if (!canShrink(file.type) || !root.URL || !root.File) { resolve(file); return; }
            var url = URL.createObjectURL(file);
            var image = new Image();
            var done = function (result) { URL.revokeObjectURL(url); resolve(result); };
            image.onerror = function () { done(file); };
            image.onload = function () {
                try {
                    // The browser has turned the photo upright already (the
                    // phone records which way up it was held).
                    var fit = fitWithin(image.naturalWidth, image.naturalHeight, MAX_SIDE);
                    if (fit.scale === 1 && file.size <= SMALL_ENOUGH_BYTES) { done(file); return; }
                    var canvas = doc.createElement('canvas');
                    canvas.width = fit.width;
                    canvas.height = fit.height;
                    canvas.getContext('2d').drawImage(image, 0, 0, fit.width, fit.height);
                    canvas.toBlob(function (blob) {
                        if (!blob || blob.size >= file.size) { done(file); return; }
                        done(new File([blob], jpegName(file.name), { type: 'image/jpeg', lastModified: file.lastModified }));
                    }, 'image/jpeg', JPEG_QUALITY);
                } catch (err) {
                    done(file);
                }
            };
            image.src = url;
        });
    }

    doc.addEventListener('alpine:init', function () {
        Alpine.data('quickPhotoAdd', function (category) {
            return {
                category: category || 'condition',
                dt: new DataTransfer(),
                items: [],
                nextKey: 0,
                preparing: 0,   // photos still being made smaller
                progress: 0,    // percent of the upload sent; 0 when none is running

                init: function () {
                    var self = this;
                    // htmx reports how much of the upload has gone.
                    this.$el.addEventListener('htmx:xhr:progress', function (e) {
                        var d = e.detail || {};
                        if (d.lengthComputable && d.total) { self.progress = Math.max(1, Math.round((d.loaded / d.total) * 100)); }
                    });
                    this.$el.addEventListener('htmx:afterRequest', function () { self.progress = 0; });
                },

                get totalMB() {
                    return this.items.reduce(function (sum, item) { return sum + item.size; }, 0) / (1024 * 1024);
                },
                get totalLabel() {
                    return sizeLabel(this.items.reduce(function (sum, item) { return sum + item.size; }, 0));
                },

                // One at a time: a phone holds a full-size photo in memory
                // while it is drawn, and four at once can be too much.
                addFiles: function (fileList) {
                    var self = this;
                    var files = Array.prototype.slice.call(fileList);
                    this.preparing += files.length;
                    return files.reduce(function (chain, file) {
                        return chain.then(function () { return shrink(file); }).then(function (ready) {
                            self.dt.items.add(ready);
                            self.items.push({
                                key: self.nextKey++,
                                name: ready.name,
                                size: ready.size,
                                url: URL.createObjectURL(ready)
                            });
                            self.preparing -= 1;
                            self.sync();
                        });
                    }, Promise.resolve());
                },

                remove: function (index) {
                    URL.revokeObjectURL(this.items[index].url);
                    this.dt.items.remove(index);
                    this.items.splice(index, 1);
                    this.sync();
                },

                sync: function () {
                    if (this.$refs.images) { this.$refs.images.files = this.dt.files; }
                }
            };
        });
    });
})(typeof window !== 'undefined' ? window : globalThis);
