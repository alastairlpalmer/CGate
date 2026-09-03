/* Quick-add photo form (templates/horses/partials/photo_form.html).
 *
 * Registered as an Alpine component so the same form works on its full
 * page and inside the pop-up sheet (where an inline <script> would race
 * Alpine's initialisation of the swapped-in markup). Accumulates files
 * from the camera and gallery inputs into one DataTransfer and mirrors
 * them onto the real, submitted `images` input.
 */
document.addEventListener('alpine:init', function () {
    Alpine.data('quickPhotoAdd', function (category) {
        return {
            category: category || 'condition',
            dt: new DataTransfer(),
            items: [],
            nextKey: 0,

            get totalMB() {
                return this.items.reduce(function (sum, item) { return sum + item.size; }, 0) / (1024 * 1024);
            },

            addFiles: function (fileList) {
                for (var i = 0; i < fileList.length; i++) {
                    var file = fileList[i];
                    this.dt.items.add(file);
                    this.items.push({
                        key: this.nextKey++,
                        name: file.name,
                        size: file.size,
                        url: URL.createObjectURL(file)
                    });
                }
                this.sync();
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
