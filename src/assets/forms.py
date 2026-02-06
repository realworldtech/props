"""Forms for the assets app."""

from django import forms

from .models import Asset, AssetImage, Category, Location, Tag

# Only these statuses are user-selectable on the form.
# Other transitions (missing, disposed) happen via specific workflows.
FORM_STATUS_CHOICES = [
    ("active", "Active"),
    ("draft", "Draft"),
    ("retired", "Retired"),
]


class AssetForm(forms.ModelForm):
    """Full asset creation/editing form."""

    status = forms.ChoiceField(
        choices=FORM_STATUS_CHOICES,
        initial="active",
        widget=forms.Select(
            attrs={
                "class": "form-input w-full rounded-lg px-4 py-2.5 text-cream",
            }
        ),
        help_text="Draft assets can be saved without category or location.",
    )

    class Meta:
        model = Asset
        fields = [
            "name",
            "description",
            "status",
            "category",
            "current_location",
            "quantity",
            "condition",
            "tags",
            "notes",
            "purchase_price",
            "estimated_value",
        ]
        widgets = {
            "name": forms.TextInput(
                attrs={
                    "class": "form-input w-full rounded-lg px-4 py-2.5 text-cream",
                    "placeholder": "Asset name",
                }
            ),
            "description": forms.Textarea(
                attrs={
                    "class": "form-input w-full rounded-lg px-4 py-2.5 text-cream",
                    "rows": 3,
                    "placeholder": "Description",
                }
            ),
            "category": forms.HiddenInput(attrs={"id": "id_category"}),
            "current_location": forms.HiddenInput(
                attrs={"id": "id_current_location"}
            ),
            "quantity": forms.NumberInput(
                attrs={
                    "class": "form-input w-full rounded-lg px-4 py-2.5 text-cream",
                    "min": 1,
                }
            ),
            "condition": forms.Select(
                attrs={
                    "class": "form-input w-full rounded-lg px-4 py-2.5 text-cream",
                }
            ),
            "tags": forms.SelectMultiple(
                attrs={
                    "class": "hidden",
                    "id": "id_tags",
                }
            ),
            "notes": forms.Textarea(
                attrs={
                    "class": "form-input w-full rounded-lg px-4 py-2.5 text-cream",
                    "rows": 3,
                }
            ),
            "purchase_price": forms.NumberInput(
                attrs={
                    "class": "form-input w-full rounded-lg px-4 py-2.5 text-cream",
                    "step": "0.01",
                    "min": "0",
                    "placeholder": "0.00",
                }
            ),
            "estimated_value": forms.NumberInput(
                attrs={
                    "class": "form-input w-full rounded-lg px-4 py-2.5 text-cream",
                    "step": "0.01",
                    "min": "0",
                    "placeholder": "0.00",
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].required = False
        self.fields["current_location"].required = False
        self.fields["category"].queryset = Category.objects.select_related(
            "department"
        )
        self.fields["current_location"].queryset = Location.objects.filter(
            is_active=True
        )

        # For edit, expand status choices if current status is beyond draft/active
        if self.instance and self.instance.pk:
            current = self.instance.status
            if current not in dict(FORM_STATUS_CHOICES):
                self.fields["status"].choices = FORM_STATUS_CHOICES + [
                    (current, current.title())
                ]

    def clean(self):
        cleaned = super().clean()
        status = cleaned.get("status")

        # Validate state transition
        if self.instance.pk and status and status != self.instance.status:
            if not self.instance.can_transition_to(status):
                allowed = Asset.VALID_TRANSITIONS.get(self.instance.status, [])
                self.add_error(
                    "status",
                    f"Cannot transition from "
                    f"'{self.instance.get_status_display()}' to "
                    f"'{status}'. Allowed: "
                    f"{', '.join(allowed) or 'none'}.",
                )

        if status and status != "draft":
            if not cleaned.get("category"):
                self.add_error(
                    "category",
                    "Category is required for non-draft assets.",
                )
            if not cleaned.get("current_location"):
                self.add_error(
                    "current_location",
                    "Location is required for non-draft assets.",
                )
        return cleaned


class AssetImageForm(forms.ModelForm):
    """Image upload form."""

    class Meta:
        model = AssetImage
        fields = ["image", "caption", "is_primary"]
        widgets = {
            "caption": forms.TextInput(
                attrs={
                    "class": "form-input w-full rounded-lg px-4 py-2.5 text-cream",
                    "placeholder": "Caption (optional)",
                }
            ),
        }


class QuickCaptureForm(forms.Form):
    """Minimal form for Quick Capture workflow."""

    name = forms.CharField(
        max_length=200,
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "form-input w-full rounded-lg px-4 py-3 text-cream text-lg",
                "placeholder": "Asset name (optional)",
            }
        ),
    )
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(
            attrs={
                "class": "form-input w-full rounded-lg px-4 py-2.5 text-cream",
                "rows": 2,
                "placeholder": "Notes (optional)",
            }
        ),
    )
    scanned_code = forms.CharField(
        max_length=200,
        required=False,
        widget=forms.HiddenInput(),
    )
    image = forms.FileField(
        required=False,
    )


class TagForm(forms.ModelForm):
    """Tag creation/editing form."""

    COLOR_CHOICES = [
        ("gray", "Gray"),
        ("red", "Red"),
        ("orange", "Orange"),
        ("yellow", "Yellow"),
        ("green", "Green"),
        ("blue", "Blue"),
        ("purple", "Purple"),
        ("pink", "Pink"),
    ]

    color = forms.ChoiceField(choices=COLOR_CHOICES)

    class Meta:
        model = Tag
        fields = ["name", "color"]
        widgets = {
            "name": forms.TextInput(
                attrs={
                    "class": "form-input w-full rounded-lg px-4 py-2.5 text-cream",
                    "placeholder": "Tag name",
                }
            ),
        }

    def clean_name(self):
        name = self.cleaned_data["name"]
        qs = Tag.objects.filter(name__iexact=name)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError(
                f"A tag with name '{name}' already exists."
            )
        return name


class CategoryForm(forms.ModelForm):
    """Category creation/editing form."""

    class Meta:
        model = Category
        fields = ["name", "description", "icon", "department"]
        widgets = {
            "name": forms.TextInput(
                attrs={
                    "class": "form-input w-full rounded-lg px-4 py-2.5 text-cream",
                }
            ),
            "description": forms.Textarea(
                attrs={
                    "class": "form-input w-full rounded-lg px-4 py-2.5 text-cream",
                    "rows": 3,
                }
            ),
            "icon": forms.HiddenInput(attrs={"id": "id_icon"}),
            "department": forms.Select(
                attrs={
                    "class": "form-input w-full rounded-lg px-4 py-2.5 text-cream",
                }
            ),
        }


class LocationForm(forms.ModelForm):
    """Location creation/editing form."""

    class Meta:
        model = Location
        fields = ["name", "address", "description", "parent", "is_active"]
        widgets = {
            "name": forms.TextInput(
                attrs={
                    "class": "form-input w-full rounded-lg px-4 py-2.5 text-cream",
                }
            ),
            "address": forms.Textarea(
                attrs={
                    "class": "form-input w-full rounded-lg px-4 py-2.5 text-cream",
                    "rows": 2,
                }
            ),
            "description": forms.Textarea(
                attrs={
                    "class": "form-input w-full rounded-lg px-4 py-2.5 text-cream",
                    "rows": 2,
                }
            ),
            "parent": forms.Select(
                attrs={
                    "class": "form-input w-full rounded-lg px-4 py-2.5 text-cream",
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            # Exclude self and descendants from parent choices
            exclude_ids = [self.instance.pk] + [
                d.pk for d in self.instance.get_descendants()
            ]
            self.fields["parent"].queryset = Location.objects.exclude(
                pk__in=exclude_ids
            )
